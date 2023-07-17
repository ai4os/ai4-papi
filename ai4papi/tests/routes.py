from ai4papi.main import app


# Check routes
routes = [(r.path, r.methods) for r in app.routes]
for collection in ['modules', 'tools']:

    assert (f'/v1/catalog/{collection}/', {'GET'}) in routes
    assert (f'/v1/catalog/{collection}/detail', {'GET'}) in routes
    assert (f'/v1/catalog/{collection}/tags', {'GET'}) in routes
    assert (f'/v1/catalog/{collection}/' + '{item_name}/config', {'GET'}) in routes
    assert (f'/v1/catalog/{collection}/' + '{item_name}/metadata', {'GET'}) in routes

    assert (f'/v1/deployments/{collection}/', {'GET'}) in routes
    assert (f'/v1/deployments/{collection}/', {'POST'}) in routes
    assert (f'/v1/deployments/{collection}/' + '{deployment_uuid}', {'GET'}) in routes
    assert (f'/v1/deployments/{collection}/' + '{deployment_uuid}', {'DELETE'}) in routes

print('Checks for API routes passed!')