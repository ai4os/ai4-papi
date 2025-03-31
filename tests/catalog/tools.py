from types import SimpleNamespace

from fastapi import Request

from ai4papi.routers.v1.catalog import common
from ai4papi.routers.v1.catalog.tools import Tools


# List tools
tools_list = list(Tools.get_items().keys())

assert isinstance(tools_list, list)
assert "ai4os-federated-server" in tools_list
assert "dogs-breed-detector" not in tools_list

# List filtered tools
tools_list2 = Tools.get_filtered_list(
    tags=("docker",),
    tags_any=None,
    not_tags=None,
    not_tags_any=None,
)
assert isinstance(tools_list2, list)
assert "ai4os-federated-server" in tools_list

# Get tools summaries
tools_sum = Tools.get_summary(
    tags=None,
    tags_any=None,
    not_tags=None,
    not_tags_any=None,
)
assert isinstance(tools_sum, list)
assert isinstance(tools_sum[0], dict)

# Get catalog tags
tools_tags = Tools.get_tags()
assert isinstance(tools_tags, list)  # empty list; deprecated method

# Explore individual tools
# Contrary than for modules, we do this for all tools because tool configurations are
# particular for each tool
for tool_name in tools_list:
    print(f"  - Testing {tool_name}")

    # Get tool config
    tool_conf = Tools.get_config(
        item_name=tool_name,
        vo="vo.ai4eosc.eu",
    )
    assert isinstance(tool_conf, dict)
    assert "general" in tool_conf.keys()

    # Get tool metadata
    tool_meta = Tools.get_metadata(
        item_name=tool_name,
    )
    assert isinstance(tool_meta, dict)
    assert "title" in tool_meta.keys()

    # Get tool metadata in different formats
    module_meta = Tools.get_metadata(
        item_name=tool_name,
        profile="mldcat",
        request=Request(
            scope={
                "type": "http",
                "headers": [(b"accept", b"application/ld+json")],
            }
        ),
    )
    assert isinstance(module_meta, dict)
    assert "@context" in module_meta.keys()

# Refresh metadata cache
common.JENKINS_TOKEN = "1234"
module_meta = Tools.refresh_metadata_cache_entry(
    item_name=tool_name,
    authorization=SimpleNamespace(
        credentials="1234",
    ),
)
assert isinstance(module_meta, dict)

# TODO: we should not be able to get config or metadata for a module_name

print("Catalog (tools) tests passed!")
