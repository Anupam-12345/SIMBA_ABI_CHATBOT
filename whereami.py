"""
Find out which copy of the code is actually running.

    python whereami.py

Run it from the directory you edited. It reports what is on disk here, whether
the patched code is present, whether a different copy exists elsewhere, and
whether a server process is running from another directory.
"""

import hashlib
import os
import subprocess
import sys

HERE = os.path.abspath(os.path.dirname(__file__) or ".")

# (relative path, marker that must exist, marker that must NOT exist)
TARGETS = [
    ("app.py",
     "def strip_context_artifacts",
     'answer_parts.append(f"\U0001F4CB **{header}**")'),
    ("config.py",
     "PARENT_TOPIC_LEVEL",
     "MAX_RESPONSE_TOKENS = 6000"),
    ("ollama_client.py",
     "_grounding_score",
     '"repeat_penalty": 1.2'),
    (os.path.join("ingestion", "build_index.py"),
     "def is_word_like",
     None),
]


def sha(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()[:12]


print("=" * 68)
print("1. FILES ON DISK IN THIS DIRECTORY")
print("=" * 68)
print(f"   {HERE}\n")

patched_here = True
for rel, must_have, must_not_have in TARGETS:
    path = os.path.join(HERE, rel)
    if not os.path.exists(path):
        print(f"   MISSING   {rel}")
        patched_here = False
        continue
    raw = open(path, encoding="utf-8", errors="replace").read()
    # Ignore commented-out code: these files contain several dead copies of
    # earlier revisions, and matching those gives false results.
    text = "\n".join(line for line in raw.splitlines() if not line.lstrip().startswith("#"))
    ok = must_have in text and (must_not_have is None or must_not_have not in text)
    patched_here &= ok
    print(f"   {'PATCHED ' if ok else 'OLD     '}  {rel:28s} sha={sha(path)}  {len(raw.splitlines())} lines")
    if not ok:
        if must_have not in text:
            print(f"             missing marker: {must_have}")
        elif must_not_have and must_not_have in text:
            print(f"             old code still present: {must_not_have}")

print()
print("=" * 68)
print("2. STALE BYTECODE")
print("=" * 68)
caches = []
for root, dirs, files in os.walk(HERE):
    dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", "vectorstore")]
    if os.path.basename(root) == "__pycache__":
        caches.append(root)
if caches:
    print(f"   {len(caches)} __pycache__ directories found. Delete them:")
    for cache in caches[:6]:
        print(f"     {cache}")
    print("\n   Linux/Mac: find . -name __pycache__ -exec rm -rf {} +")
    print("   Windows:   Get-ChildItem -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force")
else:
    print("   none")

print()
print("=" * 68)
print("3. OTHER COPIES OF THIS PROJECT ON THIS MACHINE")
print("=" * 68)
roots = [os.path.expanduser("~"), "/opt", "/srv", "/var/www", "C:\\", "D:\\"]
found = []
for root in roots:
    if not os.path.isdir(root):
        continue
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in
                       ("node_modules", "__pycache__", "site-packages", "vectorstore",
                        "Windows", "Program Files", "Program Files (x86)", "AppData")]
        if "app.py" in filenames and "ollama_client.py" in filenames:
            if os.path.abspath(dirpath) != HERE:
                found.append(os.path.abspath(dirpath))
        if len(found) >= 12:
            break
    if len(found) >= 12:
        break

if found:
    print("   OTHER COPIES FOUND — you may have edited the wrong one:\n")
    for path in found:
        try:
            text = open(os.path.join(path, "app.py"), encoding="utf-8", errors="replace").read()
            state = "PATCHED" if "def strip_context_artifacts" in text else "OLD"
        except Exception:
            state = "?"
        print(f"     [{state}]  {path}")
else:
    print("   no other copy found")

print()
print("=" * 68)
print("4. RUNNING SERVER PROCESSES")
print("=" * 68)
try:
    if os.name == "nt":
        out = subprocess.run(["wmic", "process", "where",
                              "name like '%python%'", "get", "ProcessId,CommandLine"],
                             capture_output=True, text=True, timeout=20).stdout
        print(out.strip() or "   none")
    else:
        out = subprocess.run(["ps", "-eo", "pid,cmd"], capture_output=True,
                             text=True, timeout=20).stdout
        hits = [line for line in out.splitlines()
                if any(k in line for k in ("app.py", "flask", "gunicorn", "waitress", "uwsgi"))
                and "whereami" not in line and "ps -eo" not in line]
        if not hits:
            print("   no Flask/gunicorn process visible from here")
        for line in hits:
            pid = line.split()[0]
            print(f"   {line.strip()[:100]}")
            try:
                cwd = os.readlink(f"/proc/{pid}/cwd")
                print(f"      running from: {cwd}")
                if os.path.abspath(cwd) != HERE:
                    print("      ^^ DIFFERENT DIRECTORY FROM THE ONE YOU EDITED")
            except Exception:
                pass
except Exception as exc:
    print(f"   could not list processes: {exc}")

print()
print("=" * 68)
print("5. CONTAINERISED DEPLOYMENT")
print("=" * 68)
markers = [f for f in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                       "Procfile", "*.service") if os.path.exists(os.path.join(HERE, f))]
if markers:
    print(f"   Found: {', '.join(markers)}")
    print("   If the app runs in a container, editing files on the host changes")
    print("   nothing until you rebuild the image or mount the directory:")
    print("     docker compose up -d --build")
else:
    print("   no Dockerfile / compose file in this directory")

print()
print("=" * 68)
if patched_here:
    print("VERDICT: files here ARE patched.")
    print("The running process must be reading from somewhere else -")
    print("check sections 3, 4 and 5 above, then restart the server.")
else:
    print("VERDICT: files here are NOT patched.")
    print("The replacement did not land in this directory. Copy the four files")
    print("here again and re-run this script before restarting anything.")
print("=" * 68)
