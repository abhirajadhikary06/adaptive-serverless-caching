import os
import sys

# Ensure current directory is in sys.path for internal imports (config, database, routes)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
