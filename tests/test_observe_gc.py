import gc
import weakref

import pytest
from pycrdt import Array, Doc, Map, Text, XmlFragment, XmlText


def change_target(target):
    if isinstance(target, Text):
        target += "a"
    elif isinstance(target, Map):
        target["key"] = "value"
    elif isinstance(target, Array):
        target.append(1)
    else:
        target.children.append(XmlText("a"))


@pytest.mark.parametrize("method", ["observe", "observe_deep"])
def test_observe_bound_method_gc(method):
    """Bound method callbacks should be garbage collected after unobserve()."""

    class Observer:
        def on_change(self, event):
            pass

    doc = Doc()
    map_ = doc.get("map", type=Map)
    observer = Observer()
    freed = []
    weakref.finalize(observer, freed.append, True)
    sub = getattr(map_, method)(observer.on_change)
    map_["key"] = "value"
    map_.unobserve(sub)
    del observer
    gc.collect()
    assert freed, "Observer was not garbage collected after unobserve()"


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["observe", "observe_subdocs"])
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_observed_doc_gc(method, asynchronous):
    doc = Doc()
    calls = []

    async def async_callback(event):
        calls.append(True)

    callback = async_callback if asynchronous else lambda event: calls.append(True)
    getattr(doc, method)(callback)
    async with doc.transaction():
        doc["map"] = Map({"doc": Doc()})
    assert calls == [True]
    ref = weakref.ref(doc)
    del doc
    gc.collect()
    gc.collect()
    assert ref() is None


@pytest.mark.parametrize("target", ["doc", "subdocs", "text", "map", "array", "xml"])
def test_observe_bound_method_cycle_gc(target):
    class Room:
        def __init__(self, doc):
            self.doc = doc
            self.calls = 0
            if target in ("doc", "subdocs"):
                method = "observe" if target == "doc" else "observe_subdocs"
                self.subscription = getattr(doc, method)(self.on_change)
            else:
                cls = {"text": Text, "map": Map, "array": Array, "xml": XmlFragment}[target]
                self.subscription = doc.get("value", type=cls).observe(self.on_change)

        def on_change(self, event):
            self.calls += 1

    doc = Doc()
    room = Room(doc)
    if target in ("doc", "subdocs"):
        doc["nested"] = Map({"doc": Doc()})
    else:
        change_target(doc["value"])
    assert room.calls == 1
    doc_ref = weakref.ref(doc)
    room_ref = weakref.ref(room)
    del room, doc
    gc.collect()
    gc.collect()
    assert room_ref() is None
    assert doc_ref() is None


@pytest.mark.parametrize("cls", [Text, Map, Array, XmlFragment])
def test_target_destruction_releases_callback(cls):
    class Callback:
        def __call__(self, event):
            calls.append(True)

        def __del__(self):
            # Target destruction can release the callback, whose finalizer may
            # in turn dispose of the subscription. Its target is already dead.
            subscription.drop()
            finalized.append(True)

    calls = []
    finalized = []
    doc = Doc()
    target = doc.get("value", type=cls)
    callback = Callback()
    ref = weakref.ref(callback)
    # Keep the low-level subscription alive independently of its target.
    subscription = target.integrated.observe(callback)
    del callback
    gc.collect()
    assert ref() is not None
    change_target(target)
    assert calls == [True]

    del target, doc
    gc.collect()
    gc.collect()
    assert ref() is None
    assert finalized == [True]
    subscription.drop()
    subscription.drop()


def test_unsubscribe_inside_callback():
    doc = Doc()
    text = doc.get("text", type=Text)
    calls = []

    def callback(event):
        calls.append(True)
        subscription.drop()
        subscription.drop()
        gc.collect()

    ref = weakref.ref(callback)
    subscription = text.integrated.observe(callback)
    del callback
    text += "a"
    gc.collect()
    assert ref() is None
    text += "b"
    assert calls == [True]


@pytest.mark.filterwarnings("ignore:Unclosed.*:ResourceWarning")
@pytest.mark.parametrize("subdocs", [False, True])
@pytest.mark.parametrize("async_transactions", [False, True])
def test_abandoned_event_stream_gc(subdocs, async_transactions):
    doc = Doc()
    stream = doc.events(subdocs=subdocs, async_transactions=async_transactions)
    doc_ref = weakref.ref(doc)
    stream_ref = weakref.ref(stream)
    # Deliberately abandon the stream without unsubscribing, as in issue #478.
    del stream, doc
    gc.collect()
    gc.collect()
    assert stream_ref() is None
    assert doc_ref() is None
