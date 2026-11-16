from ai4papi.routers.v1.catalog.llms_self import SelfLLMs


# List
llms_list = list(SelfLLMs.get_items().keys())

assert isinstance(llms_list, list)

# Get summaries
llms_sum = SelfLLMs.get_summary()
assert isinstance(llms_sum, list)
assert isinstance(llms_sum[0], dict)

print("🟢 Catalog (llms_self) tests passed!")
