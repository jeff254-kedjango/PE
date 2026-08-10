"""Safety guard: the synthetic seeder must never overwrite real ('insar') data.

This pins the invariant "once real InSAR data exists for an AOI, synthetic
seeding does not touch it" (without --force). Mixing fabricated velocity into a
real, life-safety dataset would be dangerous — this test ensures a future edit
can't silently drop the guard.

We test the decision predicate directly rather than running the full seeder
(which writes parquet + builds DuckDB), so the test is fast and side-effect
free. The predicate mirrors the gate in seed_synthetic.main().
"""
import pytest

from scripts import provenance


def _should_skip(prov: str, force: bool) -> bool:
    """Mirror of the gate in seed_synthetic.main(): skip iff real data and not forced."""
    return prov == "insar" and not force


@pytest.mark.parametrize("prov,force,skip", [
    ("insar",     False, True),    # real data, no force → MUST skip
    ("insar",     True,  False),   # real data, forced → overwrite (operator intent)
    ("partial",   False, False),   # synthetic velocity stand-in → re-seed OK
    ("synthetic", False, False),   # fully synthetic → re-seed OK
])
def test_skip_decision(prov, force, skip):
    assert _should_skip(prov, force) is skip


def test_seed_main_skips_insar_aoi(tmp_path, monkeypatch):
    """Integration-ish: with an AOI flagged insar, main() must not call
    write_partition for it. We stub the heavy writers and assert the skip."""
    from scripts import seed_synthetic as seed

    # Point provenance at a temp sidecar so we don't touch the real one.
    monkeypatch.setattr(provenance, "PROVENANCE_PATH", tmp_path / "prov.json")
    # seed_synthetic imported get_provenance/set_provenance by name — repoint those too.
    monkeypatch.setattr(seed, "get_provenance", provenance.get_provenance)

    # Mark the first registry AOI as real.
    real_aoi = seed.aois.REGISTRY[0].code
    provenance.set_provenance(real_aoi, "insar")

    written = []
    generated = []
    monkeypatch.setattr(seed, "write_partition",
                        lambda table, code, rows: written.append((table, code)))
    monkeypatch.setattr(seed, "write_synthetic_coh_series", lambda *a, **k: None)
    monkeypatch.setattr(seed, "write_aoi_registry", lambda: None)
    monkeypatch.setattr(seed, "build_duckdb", lambda: None)

    # Record which AOIs actually get generated; return empty-ish tables so the
    # downstream (stubbed) writers are happy.
    import pyarrow as pa

    def _fake_generate(*, aoi, **k):
        generated.append(aoi.code)
        empty = pa.table({"building_id": pa.array([], type=pa.int64())})
        return empty, empty, empty
    monkeypatch.setattr(seed, "generate_aoi_dataset", _fake_generate)
    # coh_series helper reads buildings.column("building_id"); our empty table has it.

    seed.main(force=False)

    # The real (insar) AOI must NOT be generated or written; the synthetic ones must be.
    assert real_aoi not in generated, f"insar AOI {real_aoi} was regenerated"
    assert all(code != real_aoi for _t, code in written), (
        f"synthetic seeder wrote partitions for insar AOI {real_aoi}: {written}"
    )
    other_aois = [a.code for a in seed.aois.REGISTRY if a.code != real_aoi]
    assert set(generated) == set(other_aois), (
        f"expected synthetic AOIs {other_aois} generated, got {generated}"
    )
