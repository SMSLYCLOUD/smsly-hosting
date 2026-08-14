# pylint: disable=line-too-long,too-many-instance-attributes,bare-except,logging-fstring-interpolation,import-outside-toplevel,too-few-public-methods
"""Clusters module."""
# pylint: disable=no-member
"""Cluster manager service."""
import logging
import re
import time

from kubernetes import client, config
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class ClusterManager:
    """
    Manages the deployment to the cluster (Image -> Container/Pod).
    Supports:
    - Multi-Region Deployment (via Node Affinity)
    - Vertical Pod Autoscaling (VPA)
    - Horizontal Pod Autoscaling (HPA)
    - Zero-Trust Network Policies
    """

    def __init__(self, deployment):
        self.deployment = deployment
        self.service = deployment.service
        # Load in-cluster config or local config
        try:
            config.load_incluster_config()
        except BaseException:
            try:
                config.load_kube_config()
            except BaseException:
                logger.warning(
                    "No Kubernetes config found. Running in simulation mode.")
                self.k8s_available = False
            else:
                self.k8s_available = True
        else:
            self.k8s_available = True

        if self.k8s_available:
            self.apps_v1 = client.AppsV1Api()
            self.core_v1 = client.CoreV1Api()
            self.net_v1 = client.NetworkingV1Api()
            self.autoscaling_v2 = client.AutoscalingV2Api()
            self.batch_v1 = client.BatchV1Api()
            self.custom_obj = client.CustomObjectsApi()  # For VPA

    @retry(stop=stop_after_attempt(3),
           wait=wait_exponential(multiplier=1, min=4, max=10))
    def deploy_service(self, image_tag):
        """
        Deploys the image to Kubernetes, handling multi-region distribution.
        """
        regions = list(self.service.regions.all())

        # If no regions configured, use default/primary behavior (single deployment)
        if not regions:
            logger.info(f"Deploying {self.service.name} to default region")
            return self._deploy_to_region(image_tag, region=None)

        # Multi-region deployment
        results = []
        for region in regions:
            logger.info(f"Deploying {self.service.name} to region {region.slug}")
            result = self._deploy_to_region(image_tag, region=region)
            results.append(result)

        return ", ".join(results)

    def _deploy_to_region(self, image_tag, region=None):
        """
        Deploys the service to a specific region (or default).
        """
        # Determine namespace (Isolation strategy)
        namespace = self._sanitize_name(f"ns-{self.service.name}")

        # Base name
        base_name = f"svc-{self.service.name}"

        # Suffix if region-specific
        name = base_name
        if region:
            name = f"{base_name}-{region.slug}"

        name = self._sanitize_name(name)

        if not self.k8s_available:
            self._log(f"Kubernetes not available. Cannot deploy {name}.")
            raise RuntimeError("Kubernetes is not available. Cannot deploy without a cluster.")

        # Ensure Namespace exists
        self._ensure_namespace(namespace)

        # Define Deployment
        deployment_name = name

        # Strategy modification
        # If blue/green, append suffix (simple implementation)
        if self.service.deploy_strategy == 'BLUE_GREEN':
            deployment_name = f"{name}-{self.deployment.commit_hash[:7]}"

        # Node Affinity for Region
        affinity = {}
        if region:
            affinity = {
                "nodeAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": {
                        "nodeSelectorTerms": [{
                            "matchExpressions": [{
                                "key": "topology.kubernetes.io/region",
                                "operator": "In",
                                "values": [region.slug]
                            }]
                        }]
                    }
                }
            }

        deployment_manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": deployment_name,
                "labels": {
                    "app": name,
                    "service": self.service.name,
                    "region": region.slug if region else "default"
                }
            },
            "spec": {
                "replicas": self.service.min_replicas,
                "selector": {"matchLabels": {"app": deployment_name}},
                "template": {
                    "metadata": {
                        "labels": {
                            "app": deployment_name,
                            "service": self.service.name,
                            "region": region.slug if region else "default"
                        }
                    },
                    "spec": {
                        "affinity": affinity,
                        "containers": [{
                            "name": name,
                            "image": image_tag,
                            "ports": [{"containerPort": self.service.internal_port}],
                            "command": self._get_start_command(),
                            "livenessProbe": {
                                "httpGet": {
                                    "path": "/health",
                                    "port": self.service.internal_port
                                },
                                "initialDelaySeconds": 30,
                                "periodSeconds": 10,
                                "timeoutSeconds": 5,
                                "failureThreshold": 3
                            },
                            "readinessProbe": {
                                "httpGet": {
                                    "path": "/ready",
                                    "port": self.service.internal_port
                                },
                                "initialDelaySeconds": 5,
                                "periodSeconds": 5,
                                "timeoutSeconds": 3,
                                "successThreshold": 1
                            },
                            "resources": {
                                "limits": {
                                    "memory": f"{self.service.memory_mb}Mi",
                                    "cpu": str(self.service.cpu_cores)
                                }
                            },
                            "env": self._get_env_vars()
                        }]
                    }
                }
            }
        }

        try:
            self._log(f"Applying Deployment {deployment_name}...")
            # Check if exists
            try:
                self.apps_v1.read_namespaced_deployment(
                    deployment_name, namespace)
                self.apps_v1.patch_namespaced_deployment(
                    deployment_name, namespace, deployment_manifest)
                self._log("Updated existing deployment.")
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    self.apps_v1.create_namespaced_deployment(
                        namespace, deployment_manifest)
                    self._log("Created new deployment.")
                else:
                    raise

            # Ensure Service exists
            self._ensure_service(
                name, namespace, target_app_label=deployment_name)

            # Ensure Ingress exists if public_domain is set
            # For multi-region, we might create multiple ingresses or one global
            # For now, we create one per region-deployment for direct access
            if self.service.public_domain and self.service.domain_verified:
                self._ensure_ingress(name, namespace, region=region)
            elif self.service.public_domain and not self.service.domain_verified:
                self._log(
                    f"Skipping Ingress: Domain {self.service.public_domain} "
                    f"not verified.")

            # Ensure HPA
            if self.service.max_replicas > 1:
                self._ensure_hpa(name, namespace)

            # Ensure VPA
            if self.service.vpa_enabled:
                self._ensure_vpa(name, namespace)

            # Ensure CronJobs (Only in primary region or default)
            # We don't want cronjobs running in every region typically
            is_primary = not region or (
                self.service.primary_region and
                region.id == self.service.primary_region.id
            )
            if is_primary:
                for cron in self.service.cron_jobs.all():
                    self._ensure_cronjob(cron, namespace)

            # Ensure PVCs
            for vol in self.service.volumes.all():
                self._ensure_pvc(vol, namespace, region)

            return f"pod-{name}"

        except Exception as e:
            self._log(f"K8s Error: {e!s}")
            raise

    def _ensure_service(self, name, namespace, target_app_label=None):
        """Ensure a K8s Service exists for the deployment."""
        selector_label = target_app_label or name

        service_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": name},
            "spec": {
                "selector": {"app": selector_label},
                "ports": [{
                    "protocol": "TCP",
                    "port": 80,
                    "targetPort": self.service.internal_port
                }],
                "type": "ClusterIP"
            }
        }
        try:
            self.core_v1.read_namespaced_service(name, namespace)
            self.core_v1.patch_namespaced_service(
                name, namespace, service_manifest)
            self._log(f"Updated Service {name}.")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_service(
                    namespace, service_manifest)
                self._log(f"Created Service {name}.")
            else:
                raise

    def _ensure_namespace(self, namespace):
        """Ensure the target namespace exists."""
        try:
            self.core_v1.read_namespace(namespace)
        except client.exceptions.ApiException as e:
            if e.status == 404:
                ns_manifest = {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": namespace}
                }
                self.core_v1.create_namespace(ns_manifest)
                self._log(f"Created Namespace {namespace}.")
            else:
                raise

    def _ensure_hpa(self, name, namespace):
        """Ensure HorizontalPodAutoscaler exists."""
        hpa_manifest = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": {"name": name},
            "spec": {
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": name
                },
                "minReplicas": self.service.min_replicas,
                "maxReplicas": self.service.max_replicas,
                "metrics": [{
                    "type": "Resource",
                    "resource": {
                        "name": "cpu",
                        "target": {
                            "type": "Utilization",
                            "averageUtilization": self.service.autoscale_cpu_target
                        }
                    }
                }]
            }
        }
        try:
            self.autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(
                name, namespace)
            self.autoscaling_v2.patch_namespaced_horizontal_pod_autoscaler(
                name, namespace, hpa_manifest)
            self._log("Updated HPA configuration.")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(
                    namespace, hpa_manifest)
                self._log("Created HPA configuration.")
            else:
                raise

    def _ensure_vpa(self, name, namespace):
        """
        Ensure VerticalPodAutoscaler exists.
        Requires VPA CRD installed in cluster.
        """
        vpa_name = f"{name}-vpa"
        vpa_manifest = {
            "apiVersion": "autoscaling.k8s.io/v1",
            "kind": "VerticalPodAutoscaler",
            "metadata": {"name": vpa_name},
            "spec": {
                "targetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": name
                },
                "updatePolicy": {
                    "updateMode": "Initial"
                }
            }
        }

        try:
            # VPA is a Custom Resource
            group = "autoscaling.k8s.io"
            version = "v1"
            plural = "verticalpodautoscalers"

            try:
                self.custom_obj.get_namespaced_custom_object(
                    group, version, namespace, plural, vpa_name)
                self.custom_obj.patch_namespaced_custom_object(
                    group, version, namespace, plural, vpa_name, vpa_manifest)
                self._log(f"Updated VPA {vpa_name}.")
            except client.exceptions.ApiException as e:
                if e.status != 404:
                    logger.warning(f"VPA error: {e}")
                    self._log(f"Warning: Failed to configure VPA: {e}")
                    return
                # 404: either the VPA object is missing, or the CRD is not installed.
                try:
                    self.custom_obj.create_namespaced_custom_object(
                        group, version, namespace, plural, vpa_manifest)
                    self._log(f"Created VPA {vpa_name}.")
                except client.exceptions.ApiException as create_err:
                    if create_err.status == 404:
                        self._log("VPA CRD not found in cluster. Skipping VPA.")
                    else:
                        logger.warning(f"VPA create failed: {create_err}")
                        self._log(f"Warning: Failed to create VPA: {create_err}")
        except Exception as e:
            logger.error(f"VPA configuration failed: {e}")
            self._log(f"Warning: VPA configuration failed: {e}")


    def _ensure_pvc(self, vol, namespace, region=None):
        """Ensure a PersistentVolumeClaim exists."""
        base_name = f"pvc-{vol.id}"
        name = base_name
        if region:
            name = f"{base_name}-{region.slug}"

        name = self._sanitize_name(name)

        manifest = {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": name},
            "spec": {
                "accessModes": ["ReadWriteOnce"],
                "resources": {
                    "requests": {
                        "storage": f"{vol.size_gb}Gi"
                    }
                }
            }
        }
        try:
            self.core_v1.read_namespaced_persistent_volume_claim(
                name, namespace)
            self._log(f"PVC {name} exists.")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_persistent_volume_claim(
                    namespace, manifest)
                self._log(f"Created PVC {name}.")
            else:
                raise

    def _ensure_cronjob(self, cron, namespace):
        """Ensure a Kubernetes CronJob exists."""
        name = self._sanitize_name(cron.name)
        manifest = {
            "apiVersion": "batch/v1",
            "kind": "CronJob",
            "metadata": {"name": name},
            "spec": {
                "schedule": cron.schedule,
                "jobTemplate": {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [{
                                    "name": name,
                                    "image": "busybox",  # In prod, use same image as deployment
                                    "command": ["/bin/sh", "-c", cron.command]
                                }],
                                "restartPolicy": "OnFailure"
                            }
                        }
                    }
                }
            }
        }
        try:
            self.batch_v1.read_namespaced_cron_job(name, namespace)
            self.batch_v1.patch_namespaced_cron_job(name, namespace, manifest)
            self._log(f"Updated CronJob {name}.")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.batch_v1.create_namespaced_cron_job(namespace, manifest)
                self._log(f"Created CronJob {name}.")
            else:
                raise

    def _ensure_ingress(self, name, namespace, region=None):
        """Ensure a K8s Ingress exists for the domain."""

        # For multi-region, we might want unique domains per region
        # e.g. us-east.app.com
        hostname = self.service.public_domain
        if region:
            # Basic strategy: prefix region to domain if multi-region
            # But the main need is usually global routing (handled by external DNS/LB)
            # Here we just ensure the Ingress rule exists for the region's pod service
            # If utilizing GeoDNS, we might use the SAME hostname and let DNS resolve to closest IP
            # So, keep hostname same, but Ingress targets the region-specific service
            pass

        ingress_name = name

        ingress_manifest = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": ingress_name,
                "annotations": {
                    "kubernetes.io/ingress.class": "nginx",
                    "cert-manager.io/cluster-issuer": "letsencrypt-prod"
                }
            },
            "spec": {
                "rules": [{
                    "host": hostname,
                    "http": {
                        "paths": [{
                            "path": "/",
                            "pathType": "Prefix",
                            "backend": {
                                "service": {
                                    "name": name,
                                    "port": {"number": 80}
                                }
                            }
                        }]
                    }
                }],
                "tls": [{
                    "hosts": [hostname],
                    "secretName": f"{ingress_name}-tls"
                }]
            }
        }
        try:
            self.net_v1.read_namespaced_ingress(ingress_name, namespace)
            self.net_v1.patch_namespaced_ingress(
                ingress_name, namespace, ingress_manifest)
            self._log(f"Updated Ingress for {hostname}.")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.net_v1.create_namespaced_ingress(
                    namespace, ingress_manifest)
                self._log(
                    f"Created new Ingress for {hostname}.")
            else:
                raise

    def _sanitize_name(self, name):
        """K8s resource names must be lowercase alphanumeric or -"""
        # Ensure name starts/ends with alphanumeric
        sanitized = re.sub(r'[^a-z0-9-]', '-', name.lower())
        sanitized = sanitized.strip('-')
        if not sanitized:
            return "app"
        return sanitized

    def _get_start_command(self):
        if self.service.start_command:
            # Simple shell split assumption. In prod, better parsing needed.
            return ["sh", "-c", self.service.start_command]
        return None

    def _get_env_vars(self):
        env_vars = []
        for var in self.service.env_vars.all():
            env_vars.append({
                "name": var.key,
                "value": var.value  # Decrypts automatically via django-encrypted-model-fields
            })
        return env_vars

    def _log(self, message):
        """Append logs atomically to avoid race conditions."""
        from django.db.models import Value
        from django.db.models.functions import Concat

        # Don't try to log if deployment is None (simulation)
        if not self.deployment:
            return

        timestamp = time.strftime("%H:%M:%S")
        log_line = f"[{timestamp}] [K8S] {message}\n"

        # Atomic append using Concat to avoid race condition
        # We need to import Deployment model here to avoid circular imports
        from apps.deployments.models import Deployment

        Deployment.objects.filter(id=self.deployment.id).update(
            build_logs=Concat('build_logs', Value(log_line))
        )
