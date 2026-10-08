import json
import math
import sys
import threading
import zlib
from pathlib import Path
from typing import Protocol

import paho.mqtt.client as mqtt

CLIENT_PREFIX = "railpulse-sim-"  # must match SIMULATOR_CLIENT_PREFIX in infra/railpulse_infra/ingest.py
# IoT Core caps each connection at 100 publishes/s; stay well below it.
MAX_RATE_PER_CONNECTION = 80


def shards_for_rate(messages_per_second: float) -> int:
    return max(1, math.ceil(messages_per_second / MAX_RATE_PER_CONNECTION))


class Publisher(Protocol):
    def publish(self, topic: str, payload: dict, key: str) -> None: ...
    def close(self) -> None: ...


class StdoutPublisher:
    """--dry-run: one JSON line per message, no AWS needed."""

    def publish(self, topic: str, payload: dict, key: str) -> None:
        sys.stdout.write(json.dumps({"topic": topic, "payload": payload}, separators=(",", ":")) + "\n")

    def close(self) -> None:
        sys.stdout.flush()


class MqttPublisher:
    def __init__(self, endpoint: str, cert_dir: Path, shards: int) -> None:
        self._clients: list[mqtt.Client] = []
        self._last: list[mqtt.MQTTMessageInfo | None] = []
        try:
            for i in range(shards):
                self._clients.append(self._connect(endpoint, cert_dir, f"{CLIENT_PREFIX}{i}"))
                self._last.append(None)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _connect(endpoint: str, cert_dir: Path, client_id: str) -> mqtt.Client:
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        client.tls_set(
            ca_certs=str(cert_dir / "AmazonRootCA1.pem"),
            certfile=str(cert_dir / "cert.pem"),
            keyfile=str(cert_dir / "private.key"),
        )
        connected = threading.Event()
        failure: list[str] = []

        def on_connect(_client, _userdata, _flags, reason_code, _properties):
            if reason_code.is_failure:
                failure.append(str(reason_code))
            connected.set()

        client.on_connect = on_connect
        client.connect(endpoint, 8883, keepalive=60)
        client.loop_start()
        if not connected.wait(timeout=15) or failure:
            client.loop_stop()
            reason = failure[0] if failure else "timed out"
            raise ConnectionError(f"{client_id} could not connect to {endpoint}: {reason}")
        return client

    def publish(self, topic: str, payload: dict, key: str) -> None:
        # Stable sensor → connection mapping keeps per-sensor ordering on one connection.
        i = zlib.crc32(key.encode()) % len(self._clients)
        info = self._clients[i].publish(topic, json.dumps(payload, separators=(",", ":")), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"publish to {topic} failed: {mqtt.error_string(info.rc)}")
        self._last[i] = info

    def close(self) -> None:
        for client, last in zip(self._clients, self._last):
            if last is not None:
                last.wait_for_publish(timeout=10)  # drain QoS1 in-flight messages
            client.loop_stop()
            client.disconnect()
