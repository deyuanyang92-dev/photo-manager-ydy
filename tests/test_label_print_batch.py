from app.services.label_print_batch import LabelPrintBatch


def test_batch_adds_in_order_and_deduplicates():
    batch = LabelPrintBatch()

    assert batch.add("sample", [2, 1, 2, "3", -1, "x"]) == 3

    assert batch.indices("sample") == [2, 1, 3]
    assert batch.count("sample") == 3


def test_batch_keeps_buckets_separate():
    batch = LabelPrintBatch()

    batch.add("sample", [0, 1])
    batch.add("tissue", [1])

    assert batch.indices("sample") == [0, 1]
    assert batch.indices("tissue") == [1]


def test_batch_prunes_indices_after_specimen_reload():
    batch = LabelPrintBatch()
    batch.add("sample", [0, 3, 4])
    batch.add("tissue", [1, 5])

    batch.prune(4)

    assert batch.indices("sample") == [0, 3]
    assert batch.indices("tissue") == [1]


def test_batch_clear_one_bucket_or_all():
    batch = LabelPrintBatch()
    batch.add("sample", [0])
    batch.add("tissue", [1])

    batch.clear("sample")
    assert batch.indices("sample") == []
    assert batch.indices("tissue") == [1]

    batch.clear()
    assert batch.indices("tissue") == []
