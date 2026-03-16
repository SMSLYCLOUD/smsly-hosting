output "k3s_server_public_ip" {
  description = "Public IP address of the K3s Control Plane Node"
  value       = aws_instance.k3s_server.public_ip
}

output "k3s_worker_ips" {
  description = "Public IP addresses of the K3s Worker Nodes"
  value       = [for instance in aws_instance.k3s_worker : instance.public_ip]
}

output "ssh_connection_string" {
  description = "Command to connect to the Control Plane"
  value       = "ssh -i ~/.ssh/${var.ssh_key_name}.pem ubuntu@${aws_instance.k3s_server.public_ip}"
}

output "kubeconfig_retrieval_cmd" {
  description = "Command to retrieve the kubeconfig file from the server to your local machine"
  value       = "scp -i ~/.ssh/${var.ssh_key_name}.pem ubuntu@${aws_instance.k3s_server.public_ip}:/etc/rancher/k3s/k3s.yaml ./kubeconfig.yaml && export KUBECONFIG=./kubeconfig.yaml && sed -i '' 's/127.0.0.1/${aws_instance.k3s_server.public_ip}/g' ./kubeconfig.yaml"
}
