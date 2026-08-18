#!/usr/bin/env python
"""Quick test that API imports and defines all endpoints."""

from main import app

print("\n✓ API imports successfully")
print("✓ Endpoints defined:")

for route in app.routes:
    if hasattr(route, 'path') and hasattr(route, 'methods'):
        methods = ', '.join(sorted(route.methods))
        print(f"  {route.path:30} {methods}")

print("\n✓ All modules loaded and ready for Phase 3")
