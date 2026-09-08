"""
Helpers for the asynchronous counterparts of REST framework's APIs.
"""
from asgiref.sync import sync_to_async


def overrides_sync_only(obj, base, *method_names):
    """
    Return `True` if `obj` is an instance of a subclass of `base` which
    overrides one of the given synchronous methods, without also overriding
    its asynchronous counterpart (the method with the same name, prefixed
    with `a`).

    The native asynchronous implementations provided by REST framework use
    this to decide whether to fall back to running the synchronous method in
    a thread. This ensures that subclasses which only customize a synchronous
    method retain their behavior when used from async views, rather than
    having it silently bypassed by the native asynchronous implementation.
    """
    cls = type(obj)
    for name in method_names:
        sync_overridden = getattr(cls, name, None) is not getattr(base, name, None)
        async_overridden = getattr(cls, 'a' + name, None) is not getattr(base, 'a' + name, None)
        if sync_overridden and not async_overridden:
            return True
    return False


def get_async_method(obj, name):
    """
    Return the asynchronous counterpart of the method `name` of `obj`, that is
    the method with the same name prefixed with `a`. Objects which do not
    provide one, such as third party classes not extending the REST framework
    base classes, have their synchronous method wrapped with `sync_to_async`.
    """
    method = getattr(obj, 'a' + name, None)
    if method is None:
        method = sync_to_async(getattr(obj, name))
    return method
