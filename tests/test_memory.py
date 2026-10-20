import unittest

from rolesim import memory


def item(i, content, kind="observation", **cues):
    return {"id": i, "type": kind, "actor": "env", "content": content, "cues": cues}


class MemoryTests(unittest.TestCase):
    def test_add_only(self):
        store = []
        memory.add(store, item("e1", "a"))
        with self.assertRaises(ValueError):
            memory.add(store, item("e1", "b"))
        self.assertEqual(store[0]["content"], "a")

    def test_short_memory_is_returned_whole_with_recap_pinned_first(self):
        store = [item("e1", "催促交付"), item("recap:r1", "上周的约定", "recap"), item("e2", "收件箱为空")]
        view = memory.recall(store, "交付")
        self.assertEqual([e["id"] for e in view], ["recap:r1", "e1", "e2"])
        self.assertFalse(any(e.get("recalled") for e in view))
        view[0]["content"] = "changed"
        self.assertEqual(store[1]["content"], "上周的约定")

    def test_long_memory_keeps_window_and_recalls_relevant_older_items(self):
        store = [item("old_promise", "合作方承诺周一前补发数据说明")]
        store += [item(f"n{i}", f"无关的寒暄{i}") for i in range(5)]
        store += [item(f"r{i}", f"最近的事件{i}") for i in range(4)]
        view = memory.recall(store, "补发的数据说明还没到", window=4, k=1)
        ids = [e["id"] for e in view]
        self.assertEqual(ids, ["old_promise", "r0", "r1", "r2", "r3"])
        self.assertTrue(view[0]["recalled"])

    def test_salience_breaks_ties_between_unrelated_items(self):
        store = [item("calm", "甲"), item("peak", "乙", pressure=.9)]
        self.assertEqual([e["id"] for e in memory.search(store, "丙丁", k=1)], ["peak"])
        self.assertEqual(memory.search([item("calm", "甲")], "丙丁"), [])

    def test_recall_is_deterministic(self):
        store = [item(f"x{i}", f"事件{i % 3}", pressure=(i % 2) / 2) for i in range(30)]
        self.assertEqual(memory.recall(store, "事件1", window=5), memory.recall(store, "事件1", window=5))


if __name__ == "__main__":
    unittest.main()
