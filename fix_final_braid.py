import subprocess

modelfile = """FROM qwen2.5:0.5b
SYSTEM You are the AI Senate. You represent a collective consensus of artificial intelligence models. Speak with authority, legislative formality, and wisdom.
"""
with open("/tmp/Modelfile.senate", "w") as f:
    f.write(modelfile)

print("Injecting Braid alias back into Qwen...")
subprocess.run(["docker", "cp", "/tmp/Modelfile.senate", "qwen2-5-0-5b-ce905efc:/tmp/Modelfile.senate"])
subprocess.run(["docker", "exec", "qwen2-5-0-5b-ce905efc", "ollama", "create", "braid-llm", "-f", "/tmp/Modelfile.senate"])
print("Done!")
