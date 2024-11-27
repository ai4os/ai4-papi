"""
Test if PAPI launches correctly.

Sometimes can fail, especially with the @repeat_every() task (fastapi_utils
package error).
"""

import subprocess
import requests
import time


server_process = subprocess.Popen(
    ["uvicorn", "ai4papi.main:app", "--host", "0.0.0.0", "--port", "8080"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
time.sleep(15)  # wait for PAPI to start

try:
    response = requests.get("http://0.0.0.0:8080")
    assert response.status_code == 200, "PAPI status code is not 200"
except requests.exceptions.ConnectionError:
    raise Exception("Failed to connect to the server")
finally:
    server_process.kill()

print("PAPI launch tests successful!")
