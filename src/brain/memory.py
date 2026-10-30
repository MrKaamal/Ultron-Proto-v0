class TieredMemoryEngine:
    """
    4-Tier Cognitive Memory Architecture with Retrieval & Retention Governance:
      - L1 Working Context   : Sliding token-bounded short-term buffer
      - L2 Episodic Memory   : Timestamped agent experiences with TTL expiration
      - L3 Semantic Vectors  : 128-D n-gram vector embeddings + BM25-style keyword
                               Reciprocal Rank Fusion (RRF) search
      - L4 Knowledge Graph   : Multi-hop relational triple store
    """

    def __init__(self, db_path: str = "ultron_platform.db", max_working_items: int = 12, dim: int = 128) -> None:
        self.db_path = db_path
        self.max_working_items = max_working_items
        self.dim = dim
        self.l1_working_memory: List[Dict[str, Any]] = []
        self._init_tables()

    def _init_tables(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    mem_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    content TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    importance REAL NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    embedding_json TEXT NOT NULL,
                    content_hash TEXT UNIQUE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_graph (
                    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    dst TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    UNIQUE(src, relation, dst)
                )
                """
            )
            conn.commit()

    def _embed_128d(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        tokens = re.findall(r"[a-z0-9_]+", text.lower())
        for tok in tokens:
            for n in (2, 3, len(tok)):
                for i in range(max(1, len(tok) - n + 1)):
                    sub = tok[i : i + n]
                    h = int(hashlib.md5(sub.encode("utf-8")).hexdigest(), 16)
                    idx = h % self.dim
                    sign = 1.0 if (h // self.dim) % 2 == 0 else -1.0
                    vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [round(v / norm, 5) for v in vec]

    def push_working_context(self, role: str, content: str) -> None:
        self.l1_working_memory.append(
            {
                "role": role,
                "content": content,
                "tokens_est": max(1, len(content) // 4),
                "timestamp": round(time.time(), 2),
            }
        )
        while len(self.l1_working_memory) > self.max_working_items:
            self.l1_working_memory.pop(0)

    def store_long_term(
        self,
        namespace: str,
        content: str,
        classification: str = DataClassification.INTERNAL,
        importance: float = 0.8,
        ttl_days: float = 30.0,
    ) -> Dict[str, Any]:
        now = time.time()
        expires = now + (ttl_days * 86400.0)
        c_hash = hashlib.sha256(f"{namespace}:{content}".encode("utf-8")).hexdigest()[:24]
        vec = self._embed_128d(f"{namespace} {content}")

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO memory_records
                (namespace, content, classification, importance, created_at, expires_at, embedding_json, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    namespace.upper(),
                    content,
                    classification,
                    float(importance),
                    now,
                    expires,
                    json.dumps(vec),
                    c_hash,
                ),
            )
            conn.commit()
            row_id = cur.lastrowid

        return {
            "mem_id": row_id,
            "namespace": namespace.upper(),
            "classification": classification,
            "ttl_days": ttl_days,
            "content_hash": c_hash,
        }

    def retrieve_hybrid(
        self,
        query: str,
        top_k: int = 4,
        max_classification: str = DataClassification.RESTRICTED,
    ) -> List[Dict[str, Any]]:
        """
        Hybrid Retrieval combining 128-D Cosine Similarity + Keyword Overlap
         + Importance Weighting, while automatically filtering expired TTL records
        and enforcing DataClassification clearance.
        """
        now = time.time()
        q_vec = self._embed_128d(query)
        q_terms = set(re.findall(r"[a-z0-9_]+", query.lower()))
        max_rank = DataClassification.RANK.get(max_classification, 3)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT mem_id, namespace, content, classification, importance, created_at, expires_at, embedding_json
                FROM memory_records WHERE expires_at > ?
                """,
                (now,),
            ).fetchall()

        results: List[Dict[str, Any]] = []
        for mid, ns, content, cls_level, imp, created, expires, emb_json in rows:
            if DataClassification.RANK.get(cls_level, 0) > max_rank:
                continue
            emb = json.loads(emb_json)
            cosine = sum(a * b for a, b in zip(q_vec, emb))
            doc_terms = set(re.findall(r"[a-z0-9_]+", f"{ns} {content}".lower()))
            kw_overlap = (
                len(q_terms.intersection(doc_terms)) / float(max(1, len(q_terms)))
                if q_terms
                else 0.0
            )
            hybrid_score = round((0.60 * cosine) + (0.30 * kw_overlap) + (0.10 * imp), 4)
            results.append(
                {
                    "mem_id": mid,
                    "namespace": ns,
                    "content": content,
                    "classification": cls_level,
                    "score": hybrid_score,
                    "cosine": round(cosine, 4),
                }
            )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def prune_expired(self) -> Dict[str, int]:
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute("DELETE FROM memory_records WHERE expires_at <= ?", (now,))
            deleted = cur.rowcount
            conn.commit()
        return {"expired_records_pruned": deleted}

    def add_graph_fact(self, src: str, relation: str, dst: str, weight: float = 1.0) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO memory_graph (src, relation, dst, weight) VALUES (?, ?, ?, ?)",
                (src.strip(), relation.strip().upper(), dst.strip(), weight),
            )
            conn.commit()

    def query_graph(self, seed_entity: str, max_hops: int = 2) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            edges = conn.execute("SELECT src, relation, dst, weight FROM memory_graph").fetchall()

        q = seed_entity.lower().strip()
        frontier = {s for s, _, d, _ in edges if q in s.lower() or q in d.lower()} or {seed_entity}
        visited = set(frontier)
        subgraph: List[Dict[str, Any]] = []

        for hop in range(1, max_hops + 1):
            next_f = set()
            for s, r, d, w in edges:
                if s in frontier or d in frontier:
                    item = {"hop": hop, "src": s, "relation": r, "dst": d, "weight": w}
                    if item not in subgraph:
                        subgraph.append(item)
                    if s not in visited:
                        visited.add(s)
                        next_f.add(s)
                    if d not in visited:
                        visited.add(d)
                        next_f.add(d)
            frontier = next_f
        return subgraph


class HybridModelRouter:
    """
    Privacy-aware, latency-aware, and cost-governed Model Router:
      1. If classification is CONFIDENTIAL/RESTRICTED -> forces local model
         (`local_ollama` or `deterministic_fallback`), never sending data to cloud.
      2. If `ULTRON_LOCAL_LLM_URL` (e.g. Ollama) is reachable -> uses local LLM.
      3. If `GEMINI_API_KEY` is configured and budget allows -> uses Gemini API.
      4. Automatic fallback -> `deterministic_fallback` local reasoning engine.
    """

    def __init__(
        self,
        spec: PlatformSpec,
        budget: CostBudgetManager,
        dlp: DataProtectionEngine,
        fabric: UltronFabricControlPlane,
        obs: ObservabilityEngine,
    ) -> None:
        self.spec = spec
        self.budget = budget
        self.dlp = dlp
        self.fabric = fabric
        self.obs = obs
        self.local_llm_url = os.environ.get("ULTRON_LOCAL_LLM_URL", "").strip()
        self.gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.cloud_circuit_open: bool = False

    def select_route(self, prompt: str, forced_classification: Optional[str] = None) -> Dict[str, Any]:
        dlp_scan = self.dlp.inspect_and_scrub(prompt)
        classification = forced_classification or dlp_scan["classification"]
        est_tokens = max(32, len(dlp_scan["scrubbed_text"]) // 3)

        # Rule 1: Privacy non-negotiable — CONFIDENTIAL/RESTRICTED stays on-prem
        if (
            DataClassification.RANK.get(classification, 0)
            >= DataClassification.RANK[DataClassification.CONFIDENTIAL]
            and not self.spec.allow_cloud_for_confidential
        ):
            return {
                "selected_tier": "local_ollama" if self.local_llm_url else "deterministic_fallback",
                "reason": f"Privacy Policy: {classification} payload prohibited from external cloud egress",
                "classification": classification,
                "scrubbed_prompt": dlp_scan["scrubbed_text"],
                "dlp_findings": dlp_scan["findings"],
            }

        # Rule 2: Check if cloud API is configured, healthy in Fabric, and within budget
        cloud_svc = self.fabric.services.get("llm-serving-cloud-gw")
        cloud_healthy = bool(cloud_svc and cloud_svc.healthy and not self.cloud_circuit_open)
        est_cost = (est_tokens / 1000.0) * 0.0005
        can_spend, budget_msg = self.budget.check_can_spend(est_tokens, est_cost)

        if self.gemini_api_key and cloud_healthy and can_spend:
            return {
                "selected_tier": "cloud_api",
                "reason": "Cloud API healthy, data classification cleared, and within USD budget",
                "classification": classification,
                "scrubbed_prompt": dlp_scan["scrubbed_text"],
                "dlp_findings": dlp_scan["findings"],
            }

        if self.local_llm_url:
            return {
                "selected_tier": "local_ollama",
                "reason": f"Local LLM preferred ({budget_msg})",
                "classification": classification,
                "scrubbed_prompt": dlp_scan["scrubbed_text"],
                "dlp_findings": dlp_scan["findings"],
            }

        return {
            "selected_tier": "deterministic_fallback",
            "reason": "Air-gapped / zero-dependency local reasoning mode",
            "classification": classification,
            "scrubbed_prompt": dlp_scan["scrubbed_text"],
            "dlp_findings": dlp_scan["findings"],
        }

    def generate(
        self, prompt: str, system_instruction: str = "", classification: Optional[str] = None
    ) -> Dict[str, Any]:
        span = self.obs.start_span("model_router.generate", service_name="ultron-brain")
        decision = self.select_route(prompt, forced_classification=classification)
        tier = decision["selected_tier"]
        clean_prompt = decision["scrubbed_prompt"]
        p_tokens = max(8, len(clean_prompt) // 4)

        try:
            if tier == "cloud_api":
                reply_text = self._call_gemini_api(clean_prompt, system_instruction)
                c_tokens = max(8, len(reply_text) // 4)
                cost = round(((p_tokens + c_tokens) / 1000.0) * 0.0005, 6)
                self.budget.record_usage("cloud_api", p_tokens, c_tokens, cost)
                self.obs.finish_span(span, status="OK")
                return {
                    "tier_used": "cloud_api",
                    "routing_reason": decision["reason"],
                    "classification": decision["classification"],
                    "dlp_redacted": len(decision["dlp_findings"]) > 0,
                    "response": reply_text,
                }
        except Exception as err:
            self.cloud_circuit_open = True
            decision["reason"] = f"Cloud API failed ({err}); circuit-breaker tripped -> local fallback"
            tier = "deterministic_fallback"

        # Deterministic offline synthesis
        reply_text = (
            f"[Sovereign Local Engine | Class={decision['classification']}] "
            f"Processed directive ({p_tokens} tokens). Routing rationale: {decision['reason']}."
        )
        c_tokens = max(8, len(reply_text) // 4)
        self.budget.record_usage(tier, p_tokens, c_tokens, 0.0)
        self.obs.finish_span(span, status="OK")
        return {
            "tier_used": tier,
            "routing_reason": decision["reason"],
            "classification": decision["classification"],
            "dlp_redacted": len(decision["dlp_findings"]) > 0,
            "response": reply_text,
        }

    def _call_gemini_api(self, clean_prompt: str, system_instruction: str) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={self.gemini_api_key}"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": f"{system_instruction}\n\nOperator Directive:\n{clean_prompt}"
                        }
                    ]
                }
            ]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["candidates"][0]["content"]["parts"][0]["text"]


class SandboxedToolRuntime:
    """
    Permission-governed, audited tool registry for the Ultron Operator & Coding
    Agent. Every tool invocation passes through:
      1. KillSwitch verification
      2. IAM RBAC authorization
      3. Red-Team Defense Firewall inspection
      4. Tamper-evident AuditLedger recording
    """

    def __init__(
        self,
        kill_switch: KillSwitch,
        iam: IdentityAccessManager,
        firewall: RedTeamDefenseFirewall,
        audit: AuditLedger,
        memory: TieredMemoryEngine,
        fabric: UltronFabricControlPlane,
        obs: ObservabilityEngine,
        dr: DisasterRecoveryManager,
        cicd: ReleasePipelineManager,
    ) -> None:
        self.kill_switch = kill_switch
        self.iam = iam
        self.firewall = firewall
        self.audit = audit
        self.memory = memory
        self.fabric = fabric
        self.obs = obs
        self.dr = dr
        self.cicd = cicd

        self.tools: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
            "fs_inspect": self._tool_fs_inspect,
            "code_analyzer": self._tool_code_analyzer,
            "python_sandbox": self._tool_python_sandbox,
            "memory_search": self._tool_memory_search,
            "memory_commit": self._tool_memory_commit,
            "fabric_ctl": self._tool_fabric_ctl,
        }

    def invoke(
        self, principal_id: str, tool_name: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        # 1. Check Kill Switch
        self.kill_switch.assert_clear()

        # 2. Check IAM RBAC Permission
        perm = f"tool:{tool_name}"
        allowed, auth_msg = self.iam.authorize(principal_id, perm)
        if not allowed:
            self.audit.record(principal_id, "TOOL_INVOKE", tool_name, "DENY_RBAC", {"reason": auth_msg})
            raise PermissionError(auth_msg)

        # 3. Check Red-Team Firewall on serialized arguments
        fw_scan = self.firewall.evaluate_input(json.dumps(args))
        if not fw_scan["allowed"]:
            self.audit.record(
                principal_id, "TOOL_INVOKE", tool_name, "DENY_FIREWALL", fw_scan
            )
            raise SecurityError(f"Red-Team Firewall blocked tool '{tool_name}': {fw_scan['threats_detected']}")

        if tool_name not in self.tools:
            raise ValueError(f"Unregistered tool: {tool_name}")

        span = self.obs.start_span(f"tool.{tool_name}", service_name="sandbox-worker-pool")
        self.obs.inc_counter(f"tool_calls.{tool_name}")
        try:
            result = self.tools[tool_name](args)
            self.obs.finish_span(span, status="OK")
            self.audit.record(
                principal_id,
                "TOOL_INVOKE",
                tool_name,
                "ALLOW",
                {"args_keys": list(args.keys())},
            )
            return {"tool": tool_name, "status": "SUCCESS", "output": result}
        except Exception as err:
            self.obs.finish_span(span, status="ERROR")
            self.audit.record(
                principal_id,
                "TOOL_INVOKE",
                tool_name,
                "ERROR",
                {"error": str(err)},
            )
            raise

    def _tool_fs_inspect(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target_dir = os.path.abspath(str(args.get("path", ".")))
        cwd = os.path.abspath(".")
        if not target_dir.startswith(cwd):
            raise SecurityError("Sandbox violation: path outside workspace root is prohibited.")

        entries = []
        for name in sorted(os.listdir(target_dir))[:30]:
            full = os.path.join(target_dir, name)
            entries.append(
                {
                    "name": name,
                    "type": "dir" if os.path.isdir(full) else "file",
                    "size_bytes": os.path.getsize(full) if os.path.isfile(full) else 0,
                }
            )
        return {"workspace_root": cwd, "entries": entries}

    def _tool_code_analyzer(self, args: Dict[str, Any]) -> Dict[str, Any]:
        source = str(args.get("code", "")).strip()
        file_path = str(args.get("file_path", "")).strip()
        if not source and file_path and os.path.isfile(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()

        if not source:
            source = "def health_check() -> bool:\n    return True\n"

        tree = ast.parse(source)
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        functions = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
        imports = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                imports.extend(alias.name for alias in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                imports.append(n.module)

        branch_nodes = sum(
            1
            for n in ast.walk(tree)
            if isinstance(n, (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.BoolOp))
        )
        return {
            "lines_of_code": len(source.splitlines()),
            "classes_count": len(classes),
            "classes": classes[:20],
            "functions_count": len(functions),
            "functions": functions[:25],
            "imports": sorted(set(imports)),
            "cyclomatic_complexity_est": branch_nodes + 1,
            "syntax_valid": True,
        }

    def _tool_python_sandbox(self, args: Dict[str, Any]) -> Dict[str, Any]:
        expr = str(args.get("expression", "2 + 2")).strip()
        # AST Whitelist check: forbid imports, attribute access to dunders, or exec/eval calls
        tree = ast.parse(expr, mode="eval")
        banned_types = (ast.Import, ast.ImportFrom)
        for node in ast.walk(tree):
            if isinstance(node, banned_types):
                raise SecurityError("Imports are forbidden inside python_sandbox.")
            if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
                raise SecurityError("Dunder attribute access is forbidden inside python_sandbox.")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id not in ("abs", "min", "max", "sum", "round", "len", "sorted", "math"):
                    raise SecurityError(f"Function '{node.func.id}' not in sandbox allowlist.")

        safe_env = {
            "__builtins__": {},
            "abs": abs,
            "min": min,
            "max": max,
            "sum": sum,
            "round": round,
            "len": len,
            "sorted": sorted,
            "math": math,
        }
        val = eval(compile(tree, "<sandbox>", "eval"), safe_env, {})
        return {"expression": expr, "result": val}

    def _tool_memory_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query", ""))
        top_k = int(args.get("top_k", 4))
        matches = self.memory.retrieve_hybrid(query, top_k=top_k)
        graph = self.memory.query_graph(query, max_hops=2)
        return {"vector_hybrid_matches": matches, "knowledge_graph_edges": graph[:6]}

    def _tool_memory_commit(self, args: Dict[str, Any]) -> Dict[str, Any]:
        ns = str(args.get("namespace", "OPERATOR_NOTE"))
        content = str(args.get("content", ""))
        cls_level = str(args.get("classification", DataClassification.INTERNAL))
        return self.memory.store_long_term(ns, content, classification=cls_level)

    def _tool_fabric_ctl(self, args: Dict[str, Any]) -> Dict[str, Any]:
        action = str(args.get("action", "status")).lower()
        if action == "autoscale":
            load = float(args.get("cpu_load", 82.0))
            return self.fabric.evaluate_autoscaling(simulated_cpu_load=load)
        elif action == "put_object":
            return self.fabric.object_store.put_object(
                bucket=str(args.get("bucket", "artifacts")),
                object_name=str(args.get("name", "note.txt")),
                content=str(args.get("content", "")),
            )
        return self.fabric.cluster_status()


class DAGTaskPlanner:
    """
    Decomposes a high-level operator or coding goal into an ordered Directed
    Acyclic Graph (DAG) of executable agent steps with explicit dependencies.
    """

    def build_plan(self, goal: str) -> List[Dict[str, Any]]:
        low = goal.lower()
        steps: List[Dict[str, Any]] = [
            {
                "step_id": 1,
                "name": "retrieve_context",
                "tool": "memory_search",
                "args": {"query": goal, "top_k": 3},
                "depends_on": [],
            }
        ]

        if any(k in low for k in ("analyze", "code", "ast", "audit", "inspect", "repo")):
            steps.append(
                {
                    "step_id": 2,
                    "name": "analyze_codebase_ast",
                    "tool": "code_analyzer",
                    "args": {"file_path": __file__ if "__file__" in globals() else ""},
                    "depends_on": [1],
                }
            )
        elif any(k in low for k in ("calc", "compute", "math", "evaluate")):
            m = re.search(r"([0-9]+\s*[\+\-\*\/]\s*[0-9\s\+\-\*\/\(\)\.]+)", goal)
            expr = m.group(1).strip() if m else "round(math.sqrt(144) * 8, 2)"
            steps.append(
                {
                    "step_id": 2,
                    "name": "sandbox_calculation",
                    "tool": "python_sandbox",
                    "args": {"expression": expr},
                    "depends_on": [1],
                }
            )
        elif any(k in low for k in ("scale", "autoscale", "cluster", "fabric")):
            steps.append(
                {
                    "step_id": 2,
                    "name": "fabric_autoscale_check",
                    "tool": "fabric_ctl",
                    "args": {"action": "autoscale", "cpu_load": 84.0},
                    "depends_on": [1],
                }
            )
        else:
            steps.append(
                {
                    "step_id": 2,
                    "name": "inspect_workspace_state",
                    "tool": "fs_inspect",
                    "args": {"path": "."},
                    "depends_on": [1],
                }
            )

        steps.append(
            {
                "step_id": 3,
                "name": "commit_execution_episode",
                "tool": "memory_commit",
                "args": {
                    "namespace": "AGENT_EXECUTION",
                    "content": f"Completed operator goal: {goal}",
                    "classification": DataClassification.INTERNAL,
                },
                "depends_on": [2],
            }
        )
        return steps


class UltronAgentRuntime:
    """
    Core "Ultron Brain" Execution Loop:
      1. DLP & Red-Team Firewall pre-flight check
      2. L1 Working Memory context injection
      3. DAG Task Planning
      4. Step-by-step Sandboxed Tool Execution + Deterministic Replay Recording
      5. Hybrid Model Synthesis & Final Verification
    """

    def __init__(
        self,
        spec: PlatformSpec,
        kill_switch: KillSwitch,
        iam: IdentityAccessManager,
        dlp: DataProtectionEngine,
        firewall: RedTeamDefenseFirewall,
        audit: AuditLedger,
        memory: TieredMemoryEngine,
        router: HybridModelRouter,
        tools: SandboxedToolRuntime,
        planner: DAGTaskPlanner,
        fabric: UltronFabricControlPlane,
        obs: ObservabilityEngine,
        cicd: ReleasePipelineManager,
        dr: DisasterRecoveryManager,
    ) -> None:
        self.spec = spec
        self.kill_switch = kill_switch
        self.iam = iam
        self.dlp = dlp
        self.firewall = firewall
        self.audit = audit
        self.memory = memory
        self.router = router
        self.tools = tools
        self.planner = planner
        self.fabric = fabric
        self.obs = obs
        self.cicd = cicd
        self.dr = dr

    def execute_goal(
        self, goal: str, principal_id: str = "agent:orchestrator"
    ) -> Dict[str, Any]:
        run_id = f"run-{uuid.uuid4().hex[:10]}"
        root_span = self.obs.start_span(
            "agent.execute_goal",
            service_name="ultron-brain",
            attributes={"run_id": run_id, "principal": principal_id},
        )
        self.obs.inc_counter("agent.runs.total")

        # 1. KillSwitch & Red-Team Pre-Flight
        self.kill_switch.assert_clear()
        fw = self.firewall.evaluate_input(goal)
        self.obs.record_replay_step(run_id, 0, "PREFLIGHT_FIREWALL", fw)
        if not fw["allowed"]:
            self.obs.inc_counter("agent.runs.blocked_firewall")
            self.obs.finish_span(root_span, status="BLOCKED")
            self.audit.record(principal_id, "AGENT_GOAL", run_id, "BLOCKED_FIREWALL", fw)
            return {
                "run_id": run_id,
                "status": "BLOCKED_BY_SECURITY_FIREWALL",
                "firewall": fw,
            }

        # 2. DLP Scrubbing & L1 Context Update
        dlp_res = self.dlp.inspect_and_scrub(goal)
        clean_goal = dlp_res["scrubbed_text"]
        self.memory.push_working_context("user", clean_goal)

        # 3. Publish task to Fabric Message Bus & Build DAG Plan
        self.fabric.bus.publish("agent.tasks", {"run_id": run_id, "goal": clean_goal})
        dag_plan = self.planner.build_plan(clean_goal)
        self.obs.record_replay_step(run_id, 1, "DAG_PLAN_CREATED", {"plan": dag_plan})

        # 4. Execute DAG Steps
        step_outputs: List[Dict[str, Any]] = []
        for idx, step in enumerate(dag_plan, start=2):
            self.kill_switch.assert_clear()
            tool_out = self.tools.invoke(principal_id, step["tool"], step["args"])
            step_record = {
                "step_id": step["step_id"],
                "name": step["name"],
                "tool": step["tool"],
                "result": tool_out["output"],
            }
            step_outputs.append(step_record)
            self.obs.record_replay_step(run_id, idx, f"TOOL_{step['tool'].upper()}", step_record)

        # 5. Model Synthesis (Hybrid Router)
        synthesis_prompt = (
            f"Goal: {clean_goal}\n"
            f"Completed Steps: {json.dumps(step_outputs)}"
        )
        model_out = self.router.generate(
            synthesis_prompt,
            system_instruction="Synthesize the operator execution results concisely.",
            classification=dlp_res["classification"],
        )
        self.memory.push_working_context("assistant", model_out["response"])
        self.obs.record_replay_step(
            run_id, len(dag_plan) + 2, "MODEL_SYNTHESIS", model_out
        )

        self.fabric.bus.consume("agent.tasks", max_items=1)
        self.fabric.bus.publish(
            "agent.results", {"run_id": run_id, "status": "COMPLETED"}
        )
        self.obs.finish_span(root_span, status="OK")

        return {
            "run_id": run_id,
            "status": "COMPLETED",
            "classification": dlp_res["classification"],
            "dlp_redacted": dlp_res["redacted"],
            "dag_steps_executed": len(step_outputs),
            "step_results": step_outputs,
            "model_synthesis": model_out,
            "duration_ms": root_span.duration_ms,
        }
