from ai4papi.routers.v1.catalog.modules import Modules


# List modules
modules_list = list(Modules.get_items().keys())

assert isinstance(modules_list, list)
assert 'dogs-breed-detector' in modules_list
assert 'ai4os-federated-server' not in modules_list

# List filtered modules
modules_list2 = Modules.get_filtered_list(
    tags=('development',),
    tags_any=None,
    not_tags=None,
    not_tags_any=None,
)
assert isinstance(modules_list2, list)
assert 'ai4os-dev-env' in modules_list2

# Get modules summaries
modules_sum = Modules.get_summary(
    tags=None,
    tags_any=None,
    not_tags=None,
    not_tags_any=None,
)
assert isinstance(modules_sum, list)
assert isinstance(modules_sum[0], dict)

# Get catalog tags
modules_tags = Modules.get_tags()
assert isinstance(modules_tags, list)
assert 'docker' in modules_tags

# Explore an individual module
module_name = modules_list[0]

# Get module config
module_conf = Modules.get_config(
    item_name=module_name,
    vo='vo.ai4eosc.eu',
)
assert isinstance(module_conf, dict)
assert 'general' in module_conf.keys()

# Get module metadata
module_meta = Modules.get_metadata(
    item_name=module_name,
)
assert isinstance(module_meta, dict)
assert 'title' in module_meta.keys()

#TODO: we should not be able to get config or metadata for a tool_name

print('Catalog (modules) tests passed!')
