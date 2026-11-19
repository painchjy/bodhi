"""`image_read detail=compact` must be a bounded receipt (<4K characters)."""
import json


def _payload(n_nodes=500, n_edges=800):
    nodes = [{"id": "n%d" % i, "label": "很长的节点标签%d" % i + "×" * 80,
              "location": "区域%d" % (i % 10), "bbox": [0.1, 0.2, 0.3, 0.4]}
             for i in range(n_nodes)]
    edges = [{"from": "n%d" % (i % n_nodes), "to": "n%d" % ((i + 1) % n_nodes),
              "from_label": "源节点标签", "to_label": "目标节点标签",
              "label": "很长的边标签" + "×" * 80, "style": "solid", "arrow": "end"}
             for i in range(n_edges)]
    return {
        "ok": True, "action": "image_read", "detail": "compact",
        "figure": {"figure_no": 1, "page": 2, "name": "deploy.png",
                   "caption": "部署图", "url": "/bodhi/ext/doc-image?x=1", "orig_bytes": 1000},
        "cache": "miss",
        "extraction_counts": {"texts": n_nodes, "lines": n_edges, "legend": 5, "uncertain": 9},
        "contract": {
            "nodes": nodes, "edges": edges,
            "annotations": [{"key": "legend", "value": "图例" + "×" * 100, "evidence": "x" * 100}
                            for _ in range(30)],
            "ambiguity": ["歧义项" + "×" * 100 for _ in range(20)],
            "watermark": "内部资料" * 20,
            "todo": [{"action": "人工看图", "assignee": "human"}],
            "gate": {"nodes": n_nodes, "edges": n_edges, "missing_bbox": 0,
                     "dangling_ids": ["n1", "n2"], "dangling_total": 2},
        },
        "vlm": {"model": "deepseek-flash", "base_url": "https://example.invalid/v1",
                "request_bytes": 1234567, "http_status": 200,
                "finish_reason": "stop", "content_chars": 3000,
                "reasoning_chars": 0, "request_dump": ["x" * 1000]},
        "usage": {"prompt_tokens": 100, "completion_tokens": 3000},
        "hint": "引用请带 bbox/evidence + confidence",
    }


def test_compact_image_receipt_is_hard_capped_and_explicit():
    import ke_doc_parse as D
    out = D._compact_image_receipt(_payload())
    encoded = json.dumps(out, ensure_ascii=False, indent=2)
    assert len(encoded) <= 3200, len(encoded)
    assert out["receipt_mode"] == "compact"
    assert out["compact_chars"] == len(encoded)
    gate = out["contract"]["gate"]
    # Counts stay honest even when labels/nodes are trimmed.
    assert gate["nodes"] == 500 and gate["edges"] == 800
    assert len(out["contract"]["nodes"]) < 500
    assert len(out["contract"]["edges"]) < 800
    assert out["extraction_omitted"] is True
    assert "bbox" not in (out["contract"]["nodes"][0] if out["contract"]["nodes"] else {})
    assert "request_dump" not in out["vlm"]
