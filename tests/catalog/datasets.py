from ai4papi.routers.v1.catalog.datasets import zenodo


# List datasets
dataset_list = zenodo.get_datasets(vo='vo.ai4eosc.eu')

assert isinstance(dataset_list, list)

print('Catalog (datasets) tests passed!')
