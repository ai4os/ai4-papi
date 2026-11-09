from ai4papi.routers.v1.catalog.llms_platform import PlatformLLMs


# List
llms_list = list(PlatformLLMs.get_items().keys())

assert isinstance(llms_list, list)
assert any("AI4EOSC/" in model for model in llms_list)

# Get summaries
llms_sum = PlatformLLMs.get_summary()
assert isinstance(llms_sum, list)
assert isinstance(llms_sum[0], dict)

print("🟢 Catalog (llms_platform) tests passed!")
