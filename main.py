# main.py
import sys
from src.bridge.spinal_bridge import SpinalBridge
from src.memory.engram_store import EngramStore
from src.swarm.hive_telemetry import HiveNodeMonitor
from src.cognition.ultron_brain import SovereignBrain


def main():
    print("=" * 68)
    print("  ULTRON / VISION SOVEREIGN MI PROTOTYPE (v0.1-ALPHA)")
    print("  [Layer 1: Swarm] [Layer 2: Ctypes IPC] [Layer 3: Engram SQLite]")
    print("  [Layer 6: 1,000 Hz C PD-Impedance & FOC Actuator Loop Online]")
    print("=" * 68)

    try:
        spinal = SpinalBridge(lib_dir="build")
    except FileNotFoundError as e:
        print(f"\n[FATAL BOOT ERROR]: {e}")
        sys.exit(1)

    memory = EngramStore(db_path="ultron_engrams.db")
    swarm = HiveNodeMonitor(node_designation="ULTRON-VISION-PRIME-01")
    brain = SovereignBrain(spinal=spinal, memory=memory, swarm=swarm)

    telemetry = swarm.get_node_telemetry(spinal.chassis.tick_count)
    print(f"Node ID      : {telemetry['node_id']} ({telemetry['os_kernel']})")
    print(f"Swarm Hash   : {telemetry['swarm_sync_hash']}")
    print("LLM API Mode : " + ("ENABLED (Gemini)" if brain.api_key else "AIR-GAPPED REFLEX MODE (Offline)"))
    print("-" * 68)
    print("Try commands: 'telemetry', 'move joint 3 to 85', 'posture combat_guard',")
    print("              'remember Vibranium chassis nominal', 'recall chassis', or 'exit'")
    print("-" * 68)

    while True:
        try:
            user_in = input("\nBoss@Sovereign-Core ~ $ ").strip()
            if not user_in:
                continue
            if user_in.lower() in ("exit", "quit", "shutdown"):
                print("[SYSTEM]: Locking joints to zero-torque safe state. Shutting down.")
                break
            output = brain.process_directive(user_in)
            print(f"\n{output}")
        except KeyboardInterrupt:
            print("\n[SYSTEM]: Emergency interrupt received. Halting.")
            break


if __name__ == "__main__":
    main()
