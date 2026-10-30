@dataclass
class FabricServiceInstance:
    service_id: str
    service_type: str
    endpoint: str
    zone: str
    healthy: bool = True
    replicas: int = 1
    min_replicas: int = 1
    max_replicas: int = 8
    cpu_util_pct: float = 28.0
    p95_latency_ms: float = 42.0
    active_Load_qps: float = 12.0


class FabricMessageBus:
    """
    Software-defined Pub/Sub Message Broker with topic queues and a Dead-Letter
    Queue (DLQ) for failed agent or infrastructure events.
    """

    def __init__(self) -> None:
        self.topics: Dict[str, List[Dict[str, Any]]] = {
            "agent.tasks": [],
            "agent.results": [],
            "fabric.alerts": [],
            "security.events": [],
        }
        self.dlq: List[Dict[str, Any]] = []

    def publish(self, topic: str, message: Dict[str, Any]) -> Dict[str, Any]:
        envelope = {
            "msg_id": uuid.uuid4().hex[:12],
            "topic": topic,
            "timestamp": round(time.time(), 3),
            "payload": message,
        }
        queue = self.topics.setdefault(topic, [])
        queue.append(envelope)
        if len(queue) > 200:
            queue.pop(0)
        return envelope

    def consume(self, topic: str, max_items: int = 5) -> List[Dict[str, Any]]:
        queue = self.topics.get(topic, [])
        items = queue[:max_items]
        self.topics[topic] = queue[max_items:]
        return items

    def stats(self) -> Dict[str, Any]:
        return {
            "topics": {k: len(v) for k, v in self.topics.items()},
            "dlq_depth": len(self.dlq),
        }


class FabricObjectStore:
    """
    Content-Addressable SHA-256 Object Storage for artifacts, prompts, code
    snapshots, and evaluation datasets.
    """

    def __init__(self, db_path: str = "ultron_platform.db") -> None:
        self.db_path = db_path
        self._init_table()

    def _init_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fabric_objects (
                    sha256_key TEXT PRIMARY KEY,
                    bucket TEXT NOT NULL,
                    object_name TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    payload_text TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def put_object(
        self, bucket: str, object_name: str, content: str, content_type: str = "text/plain"
    ) -> Dict[str, Any]:
        raw = content.encode("utf-8")
        sha_key = hashlib.sha256(raw).hexdigest()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fabric_objects
                (sha256_key, bucket, object_name, content_type, size_bytes, payload_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sha_key, bucket, object_name, content_type, len(raw), content, time.time()),
            )
            conn.commit()
        return {
            "bucket": bucket,
            "object_name": object_name,
            "sha256_key": sha_key[:20],
            "size_bytes": len(raw),
        }

    def get_object_by_name(self, bucket: str, object_name: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT sha256_key, bucket, object_name, content_type, size_bytes, payload_text, created_at
                FROM fabric_objects WHERE bucket = ? AND object_name = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (bucket, object_name),
            ).fetchone()
        if not row:
            return None
        return {
            "sha256_key": row[0],
            "bucket": row[1],
            "object_name": row[2],
            "content_type": row[3],
            "size_bytes": row[4],
            "content": row[5],
            "created_at": round(row[6], 2),
        }

    def list_objects(self, bucket: Optional[str] = None) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            if bucket:
                rows = conn.execute(
                    "SELECT bucket, object_name, sha256_key, size_bytes FROM fabric_objects WHERE bucket = ?",
                    (bucket,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT bucket, object_name, sha256_key, size_bytes FROM fabric_objects"
                ).fetchall()
        return [
            {
                "bucket": r[0],
                "object_name": r[1],
                "sha256_key": r[2][:16],
                "size_bytes": r[3],
            }
            for r in rows
        ]


class UltronFabricControlPlane:
    """
    Software-Defined Data Center ("Ultron Fabric") Control Plane.
    Manages cluster service registration, health-checked traffic routing,
    autoscaling of worker pools, message bus queues, and object storage.
    """

    def __init__(self, db_path: str, obs: ObservabilityEngine) -> None:
        self.obs = obs
        self.bus = FabricMessageBus()
        self.object_store = FabricObjectStore(db_path=db_path)
        self.services: Dict[str, FabricServiceInstance] = {
            "llm-serving-local": FabricServiceInstance(
                service_id="llm-serving-local",
                service_type="model_serving",
                endpoint="http://127.0.0.1:11434",
                zone="onprem-zone-a",
                replicas=2,
                min_replicas=1,
                max_replicas=6,
                cpu_util_pct=34.5,
                p95_latency_ms=65.0,
            ),
            "llm-serving-cloud-gw": FabricServiceInstance(
                service_id="llm-serving-cloud-gw",
                service_type="model_serving",
                endpoint="https://generativelanguage.googleapis.com",
                zone="cloud-edge-global",
                replicas=1,
                min_replicas=1,
                max_replicas=4,
                cpu_util_pct=14.0,
                p95_latency_ms=420.0,
            ),
            "memory-vector-shard": FabricServiceInstance(
                service_id="memory-vector-shard",
                service_type="vector_db",
                endpoint="sqlite://ultron_platform.db#l3_vectors",
                zone="onprem-zone-a",
                replicas=2,
                min_replicas=1,
                max_replicas=4,
                cpu_util_pct=22.0,
                p95_latency_ms=4.2,
            ),
            "sandbox-worker-pool": FabricServiceInstance(
                service_id="sandbox-worker-pool",
                service_type="agent_worker",
                endpoint="local://sandbox-executor",
                zone="onprem-zone-a",
                replicas=3,
                min_replicas=2,
                max_replicas=12,
                cpu_util_pct=41.0,
                p95_latency_ms=18.5,
            ),
            "telemetry-ingestor": FabricServiceInstance(
                service_id="telemetry-ingestor",
                service_type="observability",
                endpoint="local://otlp-collector",
                zone="onprem-zone-a",
                replicas=1,
                min_replicas=1,
                max_replicas=3,
                cpu_util_pct=19.0,
                p95_latency_ms=2.1,
            ),
        }

    def route_healthy_service(self, service_type: str) -> Optional[FabricServiceInstance]:
        candidates = [
            s for s in self.services.values()
            if s.service_type == service_type and s.healthy
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda s: s.p95_latency_ms)
        return candidates[0]

    def set_service_health(self, service_id: str, healthy: bool) -> Dict[str, Any]:
        if service_id not in self.services:
            raise KeyError(f"Unknown service_id: {service_id}")
        self.services[service_id].healthy = healthy
        self.bus.publish(
            "fabric.alerts",
            {"service_id": service_id, "healthy": healthy, "event": "HEALTH_STATE_CHANGED"},
        )
        return self.cluster_status()

    def evaluate_autoscaling(self, simulated_cpu_load: Optional[float] = None) -> Dict[str, Any]:
        """
        Autoscales service replicas based on CPU utilization (>70% scale out,
        <25% scale in) and queue depth on `agent.tasks`.
        """
        decisions: List[Dict[str, Any]] = []
        queue_depth = len(self.bus.topics.get("agent.tasks", []))

        for svc in self.services.values():
            if simulated_cpu_load is not None and svc.service_type == "agent_worker":
                svc.cpu_util_pct = float(simulated_cpu_load)

            old_rep = svc.replicas
            if (svc.cpu_util_pct > 70.0 or queue_depth > 10) and svc.replicas < svc.max_replicas:
                svc.replicas = min(svc.max_replicas, svc.replicas + 2)
                svc.cpu_util_pct = round(svc.cpu_util_pct * (old_rep / svc.replicas), 1)
                decisions.append(
                    {"service": svc.service_id, "action": "SCALE_OUT", "from": old_rep, "to": svc.replicas}
                )
            elif svc.cpu_util_pct < 25.0 and queue_depth == 0 and svc.replicas > svc.min_replicas:
                svc.replicas = max(svc.min_replicas, svc.replicas - 1)
                decisions.append(
                    {"service": svc.service_id, "action": "SCALE_IN", "from": old_rep, "to": svc.replicas}
                )

            self.obs.set_gauge(f"fabric.replicas.{svc.service_id}", svc.replicas)

        return {"autoscaler_decisions": decisions, "cluster": self.cluster_status()}

    def cluster_status(self) -> Dict[str, Any]:
        return {
            "services": {
                k: {
                    "type": v.service_type,
                    "endpoint": v.endpoint,
                    "zone": v.zone,
                    "healthy": v.healthy,
                    "replicas": f"{v.replicas}/{v.max_replicas}",
                    "cpu_pct": v.cpu_util_pct,
                    "p95_ms": v.p95_latency_ms,
                }
                for k, v in self.services.items()
            },
            "message_bus": self.bus.stats(),
            "stored_objects_count": len(self.object_store.list_objects()),
        }

