---
name: code-reviewer
description: "Review Python code for bugs, security issues, performance problems, and best practices"
tags: [python, code-review, security, quality, debugging]
---

## Purpose
Analyzes Python code snippets and returns structured review feedback covering:
correctness, security vulnerabilities, performance, and style improvements.

## Code
```python
def run(task: str, context: str = "") -> str:
    code = context if context.strip() else task

    checks = []

    # Security checks
    dangerous = ["eval(", "exec(", "os.system(", "subprocess.call(", "pickle.loads("]
    for d in dangerous:
        if d in code:
            checks.append(f"⚠️  SECURITY: `{d}` found — potential code injection risk")

    # Common bugs
    if "except:" in code and "except Exception" not in code:
        checks.append("🐛 BUG: Bare `except:` catches SystemExit/KeyboardInterrupt — use `except Exception:`")

    if "== None" in code:
        checks.append("🐛 STYLE: Use `is None` instead of `== None`")

    if "open(" in code and "with open(" not in code:
        checks.append("🐛 RESOURCE: Use `with open(...)` to ensure file is properly closed")

    # Performance
    if "+ str(" in code or '+ "' in code:
        checks.append("⚡ PERF: String concatenation in loop — use f-strings or str.join()")

    if not checks:
        checks.append("✅ No obvious issues found in the snippet.")

    review = "\n".join(checks)
    return f"## Code Review\n\n{review}\n\n---\n*Reviewed {len(code.splitlines())} lines*"
```
