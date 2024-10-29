"""
Test FastAPI is correctly generating the routes
"""

from ai4papi.main import app


# Check routes
routes = [(r.path, r.methods) for r in app.routes]

for collection in ['modules', 'tools']:

    assert (f'/v1/catalog/{collection}', {'GET'}) in routes
    assert (f'/v1/catalog/{collection}/detail', {'GET'}) in routes
    assert (f'/v1/catalog/{collection}/tags', {'GET'}) in routes
    assert (f'/v1/catalog/{collection}/' + '{item_name}/config', {'GET'}) in routes
    assert (f'/v1/catalog/{collection}/' + '{item_name}/metadata', {'GET'}) in routes

    assert (f'/v1/deployments/{collection}', {'GET'}) in routes
    assert (f'/v1/deployments/{collection}', {'POST'}) in routes
    assert (f'/v1/deployments/{collection}/' + '{deployment_uuid}', {'GET'}) in routes
    assert (f'/v1/deployments/{collection}/' + '{deployment_uuid}', {'DELETE'}) in routes


assert ('/v1/datasets/zenodo', {'POST'}) in routes

assert ('/v1/inference/oscar/cluster', {'GET'}) in routes
assert ('/v1/inference/oscar/services', {'GET'}) in routes
assert ('/v1/inference/oscar/services', {'POST'}) in routes
assert ('/v1/inference/oscar/services/{service_name}', {'GET'}) in routes
assert ('/v1/inference/oscar/services/{service_name}', {'PUT'}) in routes
assert ('/v1/inference/oscar/services/{service_name}', {'DELETE'}) in routes

assert ('/v1/secrets', {'GET'}) in routes
assert ('/v1/secrets', {'POST'}) in routes
assert ('/v1/secrets', {'DELETE'}) in routes

assert ('/v1/deployments/stats/user', {'GET'}) in routes
assert ('/v1/deployments/stats/cluster', {'GET'}) in routes

assert ('/v1/try_me/nomad', {'POST'}) in routes
assert ('/v1/try_me/nomad/{deployment_uuid}', {'GET'}) in routes

assert ('/v1/storage/{storage_name}/ls', {'GET'}) in routes

print('Checks for API routes passed!')
