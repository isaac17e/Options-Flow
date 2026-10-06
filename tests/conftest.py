"""
config.py exige POLYGON_API_KEY al importarse. Los tests no llaman a la red,
así que basta con una key ficticia si no hay una real en el entorno.
"""

import os

os.environ.setdefault("POLYGON_API_KEY", "test-key")
