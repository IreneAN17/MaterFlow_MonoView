def create_backend(backend_config):
    from .transformers_backend import TransformersBackend
    return TransformersBackend(backend_config)
