import unittest

from rolesim.stage import memory


def item(i, content, **cues):
    return {"id": i, "content": content, "cues": cues}


class MemoryTests(unittest.TestCase):
    def test_overlap_picks_the_relevant_entry(self):
        store = [item("old_promise", "合作方承诺周一前补发数据说明")] + [item(f"n{i}", f"无关的寒暄{i}") for i in range(5)]
        self.assertEqual([e["id"] for e in memory.search(store, "补发的数据说明还没到", k=1)], ["old_promise"])

    def test_salience_breaks_ties_between_unrelated_items(self):
        store = [item("calm", "甲"), item("peak", "乙", pressure=.9)]
        self.assertEqual([e["id"] for e in memory.search(store, "丙丁", k=1)], ["peak"])
        self.assertEqual(memory.search([item("calm", "甲")], "丙丁"), [])

    def test_ties_go_to_newer_entries_and_k_caps_the_result(self):
        store = [item(f"x{i}", "同样的内容") for i in range(6)]
        self.assertEqual([e["id"] for e in memory.search(store, "同样的内容", k=3)], ["x5", "x4", "x3"])

    def test_search_is_deterministic(self):
        store = [item(f"x{i}", f"事件{i % 3}", pressure=(i % 2) / 2) for i in range(30)]
        self.assertEqual(memory.search(store, "事件1"), memory.search(store, "事件1"))


if __name__ == "__main__":
    unittest.main()
