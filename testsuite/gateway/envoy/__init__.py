"""Envoy Gateway module"""

import time
from typing import Optional

import openshift_client as oc

from testsuite.certificates import Certificate
from testsuite.gateway import Gateway
from testsuite.kubernetes import Selector
from testsuite.kubernetes.client import KubernetesClient
from testsuite.gateway.envoy.config import EnvoyConfig
from testsuite.kubernetes.deployment import Deployment, VolumeMount, ConfigMapVolume
from testsuite.kubernetes.service import Service, ServicePort


class Envoy(Gateway):  # pylint: disable=too-many-instance-attributes
    """Envoy deployed from template"""

    @property
    def reference(self) -> dict[str, str]:
        raise AttributeError("Not Supported for Envoy-only deployment")

    def __init__(self, cluster: "KubernetesClient", name, authorino, image, labels: dict[str, str]) -> None:
        self._cluster = cluster
        self.authorino = authorino
        self.name = name
        self.image = image
        self.labels = labels

        self.deployment = None
        self.service = None
        self._config = None

    @property
    def config(self):
        """Returns EnvoyConfig instance"""
        if not self._config:
            self._config = EnvoyConfig.create_instance(self.cluster, self.name, self.authorino, self.labels)
        return self._config

    @property
    def cluster(self) -> "KubernetesClient":
        return self._cluster

    @property
    def service_name(self) -> str:
        return self.name

    def external_ip(self) -> str:
        return f"{self.service.external_ip}:{self.service.get_port('api').port}"  # type: ignore

    def rollout(self):
        """Restarts Envoy to apply newest config changes"""
        self.cluster.do_action("rollout", ["restart", f"deployment/{self.name}"])
        self.wait_for_ready()
        time.sleep(3)  # or some reason wait_for_ready is not enough, needs more investigation

    def wait_for_ready(self, timeout: int = 10 * 60):
        with oc.timeout(timeout):
            assert self.cluster.do_action(
                "rollout", ["status", f"deployment/{self.name}"]
            ), "Envoy wasn't ready in time"

    def create_deployment(self) -> Deployment:
        """Creates Deployment object for Envoy, which is then committed"""
        return Deployment.create_instance(
            self.cluster,
            self.name,
            container_name="envoy",
            image=self.image,
            ports={"api": 8080, "admin": 8001},
            selector=Selector(matchLabels={"deployment": self.name, **self.labels}),
            labels=self.labels,
            command_args=[
                "--config-path /usr/local/etc/envoy/envoy.yaml",
                "--log-level info",
                "--component-log-level filter:trace,http:debug,router:debug",
            ],
            volumes=[ConfigMapVolume(config_map_name=self.name, name="config", items={"envoy.yaml": "envoy.yaml"})],
            volume_mounts=[VolumeMount(mountPath="/usr/local/etc/envoy", name="config", readOnly=True)],
            readiness_probe={"httpGet": {"path": "/ready", "port": 8001}, "initialDelaySeconds": 3, "periodSeconds": 4},
        )

    def commit(self):
        """Deploy all required objects into cluster"""
        self.config.commit()

        self.deployment = self.create_deployment()
        self.deployment.commit()
        self.deployment.wait_for_ready()

        self.service = Service.create_instance(
            self.cluster,
            self.name,
            selector={"deployment": self.name, **self.labels},
            ports=[ServicePort(name="api", port=8080, targetPort="api")],
            service_type="LoadBalancer",
        )
        self.service.commit()

    def get_tls_cert(self, _) -> Optional[Certificate]:
        return None

    def delete(self):
        """Destroy all objects this instance created"""
        self.config.delete()
        self._config = None
        with self.cluster.context:
            if self.deployment:
                self.deployment.delete()
                self.deployment = None
            if self.service:
                self.service.delete()
                self.service = None
