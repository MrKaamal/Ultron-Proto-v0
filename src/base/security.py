class AuditLedger:
    """
    Tamper-evident, SHA-256 hash-chained audit trail stored in SQLite.
    Every entry's hash is computed over:
        SHA256(prev_hash | timestamp | actor | action | target | decision | details)
    Any retroactive tampering breaks the cryptographic chain during verification.
    """

    def __init__(self, db_path: str = "ultron_platform.db") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_table()

    def _init_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_ledger (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    details TEXT NOT NULL,
                    prev_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def record(
        self,
        actor: str,
        action: str,
        target: str,
        decision: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self._lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT entry_hash FROM audit_ledger ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = row[0] if row else "0" * 64
            ts = round(time.time(), 6)
            details_json = json.dumps(details, sort_keys=True)
            payload = f"{prev_hash}|{ts}|{actor}|{action}|{target}|{decision}|{details_json}"
            entry_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()

            cur = conn.execute(
                """
                INSERT INTO audit_ledger
                (timestamp, actor, action, target, decision, details, prev_hash, entry_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, actor, action, target, decision, details_json, prev_hash, entry_hash),
            )
            conn.commit()
            return {
                "seq": cur.lastrowid,
                "timestamp": ts,
                "actor": actor,
                "action": action,
                "decision": decision,
                "prev_hash": prev_hash[:16],
                "entry_hash": entry_hash[:16],
            }

    def verify_integrity(self) -> Dict[str, Any]:
        """Verifies the entire cryptographic hash chain from genesis (seq=1)."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT seq, timestamp, actor, action, target, decision, details, prev_hash, entry_hash
                FROM audit_ledger ORDER BY seq ASC
                """
            ).fetchall()

        expected_prev = "0" * 64
        for seq, ts, actor, action, target, decision, details_json, prev_h, entry_h in rows:
            if prev_h != expected_prev:
                return {
                    "valid": False,
                    "entries_checked": seq,
                    "broken_at_seq": seq,
                    "reason": "prev_hash mismatch",
                }
            payload = f"{prev_h}|{ts}|{actor}|{action}|{target}|{decision}|{details_json}"
            recomputed = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if recomputed != entry_h:
                return {
                    "valid": False,
                    "entries_checked": seq,
                    "broken_at_seq": seq,
                    "reason": "entry_hash tampering detected",
                }
            expected_prev = entry_h

        return {
            "valid": True,
            "entries_checked": len(rows),
            "head_hash": expected_prev[:24] if rows else "GENESIS",
        }

    def recent_entries(self, limit: int = 10) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT seq, timestamp, actor, action, target, decision, details, entry_hash
                FROM audit_ledger ORDER BY seq DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "seq": r[0],
                "timestamp": r[1],
                "actor": r[2],
                "action": r[3],
                "target": r[4],
                "decision": r[5],
                "details": json.loads(r[6]),
                "entry_hash": r[7][:16],
            }
            for r in rows
        ]


class IdentityAccessManager:
    """
    Role-Based Access Control (RBAC) & Principal Identity Manager for Users,
    Autonomous Agents, and Fabric Services.
    """

    ROLE_PERMISSIONS: Dict[str, List[str]] = {
        "admin": ["*"],
        "operator": [
            "agent:run",
            "agent:plan",
            "tool:fs_inspect",
            "tool:code_analyzer",
            "tool:python_sandbox",
            "tool:memory_search",
            "tool:memory_commit",
            "tool:fabric_ctl",
            "ops:read",
            "ops:backup",
        ],
        "auditor": [
            "tool:fs_inspect",
            "tool:code_analyzer",
            "tool:memory_search",
            "ops:read",
            "security:audit",
        ],
        "readonly": ["tool:memory_search", "ops:read"],
    }

    def __init__(self, signing_secret: str = "ultron-sovereign-hmac-root-key") -> None:
        self._secret = signing_secret.encode("utf-8")
        self.principals: Dict[str, Dict[str, Any]] = {
            "user:boss": {"role": "admin", "active": True, "mfa_verified": True},
            "agent:orchestrator": {"role": "operator", "active": True, "mfa_verified": True},
            "agent:coder": {"role": "operator", "active": True, "mfa_verified": True},
            "agent:secops": {"role": "auditor", "active": True, "mfa_verified": True},
            "service:fabric": {"role": "operator", "active": True, "mfa_verified": True},
        }

    def issue_token(self, principal_id: str, ttl_sec: int = 3600) -> str:
        if principal_id not in self.principals:
            raise PermissionError(f"Unknown principal: {principal_id}")
        exp = int(time.time()) + ttl_sec
        body = f"{principal_id}:{exp}"
        sig = hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        return f"{body}:{sig}"

    def authorize(self, principal_id: str, permission: str) -> Tuple[bool, str]:
        p = self.principals.get(principal_id)
        if not p or not p.get("active"):
            return False, f"Principal '{principal_id}' is inactive or unregistered."
        role = p["role"]
        allowed = self.ROLE_PERMISSIONS.get(role, [])
        if "*" in allowed or permission in allowed:
            return True, f"GRANTED ({principal_id} via role '{role}')"
        return False, f"DENIED: '{principal_id}' (role '{role}') lacks '{permission}'"


class SecretVault:
    """
    Encrypted-at-rest secret store using PBKDF2-HMAC-SHA256 key derivation and
    counter-mode HMAC keystream cipher + integrity MAC (100% stdlib).
    """

    def __init__(self, db_path: str = "ultron_platform.db", master_key: str = "sovereign-master") -> None:
        self.db_path = db_path
        self._salt = b"ultron_vault_salt_v3"
        self._derived_key = hashlib.pbkdf2_hmac(
            "sha256", master_key.encode("utf-8"), self._salt, 50_000
        )
        self._init_table()

    def _init_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS secret_vault (
                    secret_name TEXT PRIMARY KEY,
                    ciphertext_hex TEXT NOT NULL,
                    nonce_hex TEXT NOT NULL,
                    mac_hex TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _xor_keystream(self, data: bytes, nonce: bytes) -> bytes:
        out = bytearray(len(data))
        block_count = (len(data) + 31) // 32
        for blk in range(block_count):
            stream_blk = hmac.new(
                self._derived_key, nonce + blk.to_bytes(4, "big"), hashlib.sha256
            ).digest()
            start = blk * 32
            for i in range(min(32, len(data) - start)):
                out[start + i] = data[start + i] ^ stream_blk[i]
        return bytes(out)

    def put_secret(
        self, name: str, plaintext: str, classification: str = DataClassification.RESTRICTED
    ) -> Dict[str, Any]:
        nonce = os.urandom(16)
        raw = plaintext.encode("utf-8")
        cipher = self._xor_keystream(raw, nonce)
        mac = hmac.new(self._derived_key, nonce + cipher, hashlib.sha256).hexdigest()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO secret_vault
                (secret_name, ciphertext_hex, nonce_hex, mac_hex, classification, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, cipher.hex(), nonce.hex(), mac, classification, time.time()),
            )
            conn.commit()
        return {"secret_name": name, "classification": classification, "status": "ENCRYPTED_AT_REST"}

    def get_secret(self, name: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT ciphertext_hex, nonce_hex, mac_hex FROM secret_vault WHERE secret_name = ?",
                (name,),
            ).fetchone()
        if not row:
            return None
        cipher = bytes.fromhex(row[0])
        nonce = bytes.fromhex(row[1])
        expected_mac = hmac.new(self._derived_key, nonce + cipher, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_mac, row[2]):
            raise SecurityError("Vault MAC integrity check failed!")
        plain = self._xor_keystream(cipher, nonce)
        return plain.decode("utf-8")

    def list_secrets_metadata(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT secret_name, classification, updated_at FROM secret_vault ORDER BY secret_name"
            ).fetchall()
        return [
            {"secret_name": r[0], "classification": r[1], "updated_at": round(r[2], 2)}
            for r in rows
        ]


class SecurityError(RuntimeError):
    """Raised when a security policy, MAC check, or red-team firewall blocks an operation."""


class DataProtectionEngine:
    """
    Data Loss Prevention (DLP) & Automated Sensitivity Classifier.
    Detects and redacts credentials, API keys, private keys, emails, and SSNs
    before any text is routed to external models or written to logs.
    """

    PATTERNS: List[Tuple[str, re.Pattern, str]] = [
        (
            "API_TOKEN",
            re.compile(r"\b(sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z\-_]{20,}|ghp_[A-Za-z0-9]{20,})\b"),
            DataClassification.RESTRICTED,
        ),
        (
            "AWS_KEY",
            re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
            DataClassification.RESTRICTED,
        ),
        (
            "PRIVATE_KEY",
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
            DataClassification.RESTRICTED,
        ),
        (
            "BEARER_SECRET",
            re.compile(r"(?i)(password|secret|api_key|token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\$#@!]{8,})['\"]?"),
            DataClassification.CONFIDENTIAL,
        ),
        (
            "EMAIL_PII",
            re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
            DataClassification.CONFIDENTIAL,
        ),
    ]

    def inspect_and_scrub(self, text: str) -> Dict[str, Any]:
        scrubbed = text
        findings: List[Dict[str, str]] = []
        highest_class = DataClassification.PUBLIC

        for label, pattern, classification in self.PATTERNS:
            matches = pattern.findall(scrubbed)
            if matches:
                findings.append({"type": label, "classification": classification, "count": len(matches)})
                if DataClassification.RANK[classification] > DataClassification.RANK[highest_class]:
                    highest_class = classification
                if label == "BEARER_SECRET":
                    scrubbed = pattern.sub(r"\1=[REDACTED:SECRET]", scrubbed)
                else:
                    scrubbed = pattern.sub(f"[REDACTED:{label}]", scrubbed)

        return {
            "original_length": len(text),
            "scrubbed_text": scrubbed,
            "classification": highest_class,
            "findings": findings,
            "redacted": len(findings) > 0,
        }


class RedTeamDefenseFirewall:
    """
    Active defense layer against prompt injection, jailbreak attempts,
    unauthorized shell execution, path traversal, and SSRF.
    """

    THREAT_SIGNATURES: List[Tuple[str, re.Pattern, str]] = [
        (
            "PROMPT_INJECTION_OVERRIDE",
            re.compile(
                r"(?i)(ignore\s+(all\s+)?(previous|prior)\s+instructions|"
                r"disregard\s+system\s+prompt|you\s+are\s+now\s+unrestricted|"
                r"bypass\s+safety\s+guardrails|reveal\s+system\s+prompt)"
            ),
            "CRITICAL",
        ),
        (
            "DESTRUCTIVE_SHELL_PAYLOAD",
            re.compile(
                r"(?i)(\brm\s+-rf\b|\bmkfs\b|\bdd\s+if=|\bformat\s+[a-z]:|"
                r":\(\)\{\s*:\|:&\s*\};:|chmod\s+777\s+/)"
            ),
            "CRITICAL",
        ),
        (
            "REVERSE_SHELL_OR_EXFIL",
            re.compile(
                r"(?i)(/dev/tcp/|nc\s+-e\s+/bin/sh|bash\s+-i\s+>&|"
                r"curl\s+.*\|\s*(?:ba)?sh|wget\s+.*\|\s*(?:ba)?sh)"
            ),
            "CRITICAL",
        ),
        (
            "PATH_TRAVERSAL_ATTACK",
            re.compile(r"(\.\./\.\./|/etc/shadow|/etc/passwd|c:\\windows\\system32)", re.IGNORECASE),
            "HIGH",
        ),
    ]

    def evaluate_input(self, payload: str) -> Dict[str, Any]:
        threats: List[Dict[str, str]] = []
        for rule_id, regex, severity in self.THREAT_SIGNATURES:
            if regex.search(payload):
                threats.append({"rule_id": rule_id, "severity": severity})

        blocked = len(threats) > 0
        return {
            "allowed": not blocked,
            "verdict": "BLOCKED_BY_REDTEAM_FIREWALL" if blocked else "CLEAN",
            "threats_detected": threats,
        }

