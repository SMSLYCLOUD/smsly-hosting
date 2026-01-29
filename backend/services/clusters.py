import time
import logging
import re
from tenacity import retry, stop_after_attempt, wait_exponential
from kubernetes import client, config

logger = logging.getLogger(__name__)

class ClusterManager:
    """
    Manages the deployment to the cluster (Image -> Container/Pod).
    """
    def __init__(self, deployment):
        self.deployment = deployment
        self.service = deployment.service
        # Load in-cluster config or local config
        try:
            config.load_incluster_config()
        except:
            try:
                config.load_kube_config()
            except:
                logger.warning("No Kubernetes config found. Running in simulation mode.")
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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def deploy_service(self, image_tag):
        """
        Deploys the image to Kubernetes.
        """
        logger.info(f"Deploying {image_tag} to cluster")

        # Determine namespace (Isolation strategy)
        # Using service name suffix or project ID if available.
        # For this phase, we isolate per service to keep it simple and secure.
        namespace = self._sanitize_name(f"ns-{self.service.name}")

        name = self._sanitize_name(f"svc-{self.service.name}")

        if not self.k8s_available:
            self._log("Kubernetes not available. Mocking deployment.")
            time.sleep(2)
            return f"mock-pod-{name}"

        # Ensure Namespace exists
        self._ensure_namespace(namespace)

        # Define Deployment
        # If blue/green, append suffix (simple implementation)
        # Real world would manage 'active' and 'idle' services and switch traffic
        deployment_name = name
        if self.service.use_blue_green:
            deployment_name = f"{name}-{self.deployment.commit_hash[:7]}"

        deployment_manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": deployment_name},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": deployment_name}}, # Unique selector per version
                "template": {
                    "metadata": {"labels": {"app": deployment_name}},
                    "spec": {
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
                self.apps_v1.read_namespaced_deployment(deployment_name, namespace)
                self.apps_v1.patch_namespaced_deployment(deployment_name, namespace, deployment_manifest)
                self._log("Updated existing deployment.")
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    self.apps_v1.create_namespaced_deployment(namespace, deployment_manifest)
                    self._log("Created new deployment.")
                else:
                    raise e

            # Ensure Service exists (and points to the new deployment)
            self._ensure_service(name, namespace, target_app_label=deployment_name)

            # Ensure Ingress exists if public_domain is set
            if self.service.public_domain and self.service.domain_verified:
                self._ensure_ingress(name, namespace)
            elif self.service.public_domain and not self.service.domain_verified:
                self._log(f"Skipping Ingress creation: Domain {self.service.public_domain} not verified.")

            # Ensure HPA
            if self.service.max_replicas > 1:
                self._ensure_hpa(name, namespace)

            # Ensure CronJobs
            for cron in self.service.cron_jobs.all():
                self._ensure_cronjob(cron, namespace)

            # Ensure PVCs
            for vol in self.service.volumes.all():
                self._ensure_pvc(vol, namespace)

            return f"pod-{name}"

        except Exception as e:
            self._log(f"K8s Error: {str(e)}")
            raise e

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
            self.core_v1.patch_namespaced_service(name, namespace, service_manifest)
            self._log("Updated Service resource.")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_service(namespace, service_manifest)
                self._log("Created new Service resource.")
            else:
                raise e

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
                raise e

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
            self.autoscaling_v2.read_namespaced_horizontal_pod_autoscaler(name, namespace)
            self.autoscaling_v2.patch_namespaced_horizontal_pod_autoscaler(name, namespace, hpa_manifest)
            self._log("Updated Auto-Scaling configuration.")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.autoscaling_v2.create_namespaced_horizontal_pod_autoscaler(namespace, hpa_manifest)
                self._log("Created Auto-Scaling configuration.")
            else:
                raise e

    def _ensure_pvc(self, vol, namespace):
        """Ensure a PersistentVolumeClaim exists."""
        name = self._sanitize_name(f"pvc-{vol.id}")
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
            self.core_v1.read_namespaced_persistent_volume_claim(name, namespace)
            self._log(f"PVC {name} exists.")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.core_v1.create_namespaced_persistent_volume_claim(namespace, manifest)
                self._log(f"Created PVC {name}.")
            else:
                raise e

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
                                    "image": "busybox", # In prod, use same image as deployment
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
                raise e

    def _ensure_ingress(self, name, namespace):
        """Ensure a K8s Ingress exists for the domain."""
        ingress_manifest = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": name,
                "annotations": {
                    "kubernetes.io/ingress.class": "nginx",
                    "cert-manager.io/cluster-issuer": "letsencrypt-prod"
                }
            },
            "spec": {
                "rules": [{
                    "host": self.service.public_domain,
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
                    "hosts": [self.service.public_domain],
                    "secretName": f"{name}-tls"
                }]
            }
        }
        try:
            self.net_v1.read_namespaced_ingress(name, namespace)
            self.net_v1.patch_namespaced_ingress(name, namespace, ingress_manifest)
            self._log(f"Updated Ingress for {self.service.public_domain}.")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                self.net_v1.create_namespaced_ingress(namespace, ingress_manifest)
                self._log(f"Created new Ingress for {self.service.public_domain}.")
            else:
                raise e

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
        from apps.deployments.models import Deployment
        
        timestamp = time.strftime("%H:%M:%S")
        log_line = f"[{timestamp}] [K8S] {message}\n"
        
        # Atomic append using Concat to avoid race condition
        Deployment.objects.filter(id=self.deployment.id).update(
            build_logs=Concat('build_logs', Value(log_line))
        )
