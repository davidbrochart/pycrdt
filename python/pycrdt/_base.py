from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from functools import lru_cache, partial
from inspect import signature
from typing import TYPE_CHECKING, Any, Callable, Type, cast

import anyio

from ._pycrdt import Doc as _Doc
from ._pycrdt import Subscription
from ._pycrdt import Transaction as _Transaction
from ._transaction import ReadTransaction, Transaction

if TYPE_CHECKING:
    from ._doc import Doc


base_types: dict[Any, type[BaseType | BaseDoc]] = {}
event_types: dict[Any, type[BaseEvent]] = {}


def forbid_read_transaction(txn: Transaction):
    if isinstance(txn, ReadTransaction):
        raise RuntimeError("Read-only transaction cannot be used to modify document structure")


class BaseDoc:
    _doc: _Doc
    _twin_doc: BaseDoc | None
    _txn: Transaction | None
    _txn_lock: threading.Lock
    _txn_async_lock: anyio.Lock
    _allow_multithreading: bool
    _Model: Any
    _subscriptions: list[Subscription]
    _origins: dict[int, Any]

    def __init__(
        self,
        *,
        client_id: int | None = None,
        doc: _Doc | None = None,
        Model=None,
        allow_multithreading: bool = False,
        **data,
    ) -> None:
        super().__init__(**data)
        if doc is None:
            doc = _Doc(client_id)
        self._doc = doc
        self._txn = None
        self._txn_lock = threading.Lock()
        self._txn_async_lock = anyio.Lock()
        self._Model = Model
        self._subscriptions = []
        self._origins = {}
        self._allow_multithreading = allow_multithreading


class BaseType(ABC):
    _doc: Doc | None
    _prelim: Any | None
    _integrated: Any
    _type_name: str
    _subscriptions: list[Subscription]

    def __init__(
        self,
        init: Any = None,
        *,
        _doc: Doc | None = None,
        _integrated: Any = None,
    ) -> None:
        self._type_name = self.__class__.__name__.lower()
        self._subscriptions = []
        # private API
        if _integrated is not None:
            self._doc = _doc
            self._prelim = None
            self._integrated = _integrated
            return
        # public API
        self._doc = None
        self._prelim = init
        self._integrated = None

    @abstractmethod
    def to_py(self) -> Any: ...

    @abstractmethod
    def _get_or_insert(self, name: str, doc: Doc) -> Any: ...

    @abstractmethod
    def _init(self, value: Any | None) -> None: ...

    def _forbid_read_transaction(self, txn: Transaction):
        forbid_read_transaction(txn)

    def _integrate(self, doc: Doc, integrated: Any) -> Any:
        prelim = self._prelim
        self._doc = doc
        self._prelim = None
        self._integrated = integrated
        return prelim

    def _do_and_integrate(self, action: str, value: BaseType, txn: _Transaction, *args) -> None:
        method = getattr(self._integrated, f"{action}_{value.type_name}_prelim")
        integrated = method(txn, *args)
        assert self._doc is not None
        prelim = value._integrate(self._doc, integrated)
        value._init(prelim)

    def _maybe_as_type_or_doc(self, obj: Any) -> Any:
        for k, v in base_types.items():
            if isinstance(obj, k):
                if issubclass(v, BaseDoc):
                    # create a BaseDoc
                    return v(doc=obj)
                # create a BaseType
                return v(_doc=self.doc, _integrated=obj)
        # that was a primitive value, just return it
        return obj

    @property
    def integrated(self) -> Any:
        if self._integrated is None:
            raise RuntimeError("Not integrated in a document yet")
        return self._integrated

    @property
    def doc(self) -> Doc:
        """
        The document this shared type belongs to.

        Raises:
            RuntimeError: Not integrated in a document yet.
        """
        if self._doc is None:
            raise RuntimeError("Not integrated in a document yet")
        return self._doc

    @property
    def is_prelim(self) -> bool:
        return self._prelim is not None

    @property
    def is_integrated(self) -> bool:
        return self._integrated is not None

    @property
    def prelim(self) -> Any:
        return self._prelim

    @property
    def type_name(self) -> str:
        return self._type_name

    def observe(self, callback: Callable[[BaseEvent], None]) -> Subscription:
        _callback = partial(observe_callback, callback, self.doc)
        subscription = self.integrated.observe(_callback)
        self._subscriptions.append(subscription)
        return subscription

    def observe_deep(self, callback: Callable[[list[BaseEvent]], None]) -> Subscription:
        """
        Subscribes a callback for all events emitted by this and nested collaborative types.

        Args:
            callback: The callback to call with the list of events.
        """
        _callback = partial(observe_deep_callback, callback, self.doc)
        subscription = self.integrated.observe_deep(_callback)
        self._subscriptions.append(subscription)
        return subscription

    def unobserve(self, subscription: Subscription) -> None:
        """
        Unsubscribes to changes using the given subscription.

        Args:
            subscription: The subscription to unregister.
        """
        self._subscriptions.remove(subscription)
        subscription.drop()


def observe_callback(
    callback: Callable[[], None] | Callable[[Any], None] | Callable[[Any, ReadTransaction], None],
    doc: Doc,
    event: Any,
):
    param_nb = count_parameters(callback)
    _event = event_types[type(event)](event, doc)
    with doc._read_transaction(event.transaction) as txn:
        params = (_event, txn)
        callback(*params[:param_nb])  # type: ignore[arg-type]


def observe_deep_callback(
    callback: Callable[[], None] | Callable[[Any], None] | Callable[[Any, ReadTransaction], None],
    doc: Doc,
    events: list[Any],
):
    param_nb = count_parameters(callback)
    for idx, event in enumerate(events):
        events[idx] = event_types[type(event)](event, doc)
    with doc._read_transaction(event.transaction) as txn:
        params = (events, txn)
        callback(*params[:param_nb])  # type: ignore[arg-type]


class BaseEvent:
    __slots__ = ()

    def __init__(self, event: Any, doc: Doc):
        slot: str
        for slot in self.__slots__:
            processed = process_event(getattr(event, slot), doc)
            setattr(self, slot, processed)

    def __str__(self):
        str_list = []
        slot: Any
        for slot in self.__slots__:
            val = str(getattr(self, slot))
            str_list.append(f"{slot}: {val}")
        ret = ", ".join(str_list)
        return "{" + ret + "}"


def process_event(value: Any, doc: Doc) -> Any:
    if isinstance(value, list):
        for idx, val in enumerate(value):
            value[idx] = process_event(val, doc)
    elif isinstance(value, dict):
        for key, val in value.items():
            value[key] = process_event(val, doc)
    else:
        val_type = type(value)
        if val_type in base_types:
            if val_type is _Doc:
                doc_type: type[BaseDoc] = cast(Type[BaseDoc], base_types[val_type])
                value = doc_type(doc=value)
            else:
                base_type = cast(Type[BaseType], base_types[val_type])
                value = base_type(_integrated=value, _doc=doc)
    return value


@lru_cache(maxsize=1024)
def count_parameters(func: Callable) -> int:
    """Count the number of parameters in a callable"""
    return len(signature(func).parameters)
