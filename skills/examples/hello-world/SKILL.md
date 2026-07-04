---
name: hello-world
description: "Simple greeting skill — demonstrates the SKILL.md format and basic skill structure"
tags: [demo, example, basic]
---

## Purpose
A minimal example skill showing how SuperAiAgent skills work.
Use this as a template when creating your first skill.

## Code
```python
def run(task: str, context: str = "") -> str:
    name = task.replace("hello", "").replace("hi", "").strip() or "World"
    return f"Hello, {name}! 👋\n\nThis response came from the hello-world skill.\nTask received: '{task}'"
```
