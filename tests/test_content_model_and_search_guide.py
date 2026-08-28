#!/usr/bin/env python3
"""
End-to-end validation of the Kaltura Content Model and Search Guide.

Covers: entry object model fields (type, objectType, mediaType), tags as a
direct entry field, the fileAsset and markdownAsset lifecycles, the real
Knowledge base content-add flow (document entry + document_documents.addContent
+ categoryEntry.add), eSearch content-gating behavior (NO_CONTENT entries are
not indexed, READY entries are found immediately), and the AI Genie Knowledge
Base record lifecycle (add, get, status semantics, delete).
"""

import sys
import os
import time
import requests

sys.path.insert(0, os.path.dirname(__file__))
from test_helpers import (
    kaltura_post, genie_post, create_test_entry, delete_test_entry,
    TestRunner, PARTNER_ID, KS, SERVICE_URL,
)

state = {}


def esearch_post(action, params):
    """POST to eSearch with form-encoded params. Returns parsed JSON."""
    data = {"ks": KS, "format": 1}
    data.update(params)
    resp = requests.post(
        f"{SERVICE_URL}/service/elasticsearch_esearch/action/{action}",
        data=data,
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, dict) and result.get("objectType") == "KalturaAPIException":
        raise Exception(f"eSearch error: {result.get('message')} (code: {result.get('code')})")
    return result


def main():
    runner = TestRunner("Content Model and Search Guide — E2E Validation")

    # ════════════════════════════════════════════
    # Phase 1: The Entry Object Model
    # ════════════════════════════════════════════

    def test_entry_object_model_fields():
        """baseEntry.get exposes objectType, type, and mediaType as documented."""
        entry_id = create_test_entry()
        state["model_entry_id"] = entry_id
        runner.register_cleanup(f"entry {entry_id}", lambda: delete_test_entry(entry_id))

        result = kaltura_post("baseEntry", "get", {"entryId": entry_id})
        assert result.get("objectType") == "KalturaMediaEntry", \
            f"Expected KalturaMediaEntry, got {result.get('objectType')}"
        assert result.get("type") == 1, f"Expected type=1 (MEDIA_CLIP), got {result.get('type')}"
        assert result.get("mediaType") == 1, f"Expected mediaType=1 (VIDEO), got {result.get('mediaType')}"
        assert result.get("status") == 7, f"Expected status=7 (NO_CONTENT), got {result.get('status')}"
        print(f"    Entry {entry_id}: objectType={result['objectType']}, "
              f"type={result['type']}, mediaType={result['mediaType']}, status={result['status']}")

    runner.run_test("baseEntry.get — objectType/type/mediaType fields", test_entry_object_model_fields)

    def test_tags_direct_field():
        """Tags live directly on the entry object — no separate service call needed."""
        entry_id = state["model_entry_id"]
        updated = kaltura_post("baseEntry", "update", {
            "entryId": entry_id,
            "baseEntry[objectType]": "KalturaMediaEntry",
            "baseEntry[tags]": "content-model-guide-test,automated",
        })
        # Kaltura normalizes the tags string by inserting a space after each comma.
        assert updated.get("tags") == "content-model-guide-test, automated", \
            f"Expected normalized tags to round-trip, got {updated.get('tags')!r}"

        fetched = kaltura_post("baseEntry", "get", {"entryId": entry_id})
        assert fetched.get("tags") == "content-model-guide-test, automated", \
            f"Expected tags on get, got {fetched.get('tags')!r}"
        print(f"    Tags round-tripped via baseEntry.update/get: {fetched['tags']!r}")

    runner.run_test("baseEntry.update/get — tags as a direct entry field", test_tags_direct_field)

    # ════════════════════════════════════════════
    # Phase 1b: fileAsset lifecycle
    # ════════════════════════════════════════════

    def test_file_asset_lifecycle():
        """fileAsset.add/setContent/get/list/delete — a generic file attached to an entry, distinguished by systemName."""
        entry_id = state["model_entry_id"]

        added = kaltura_post("fileAsset", "add", {
            "fileAsset[objectType]": "KalturaFileAsset",
            "fileAsset[fileAssetObjectType]": 3,  # ENTRY
            "fileAsset[objectId]": entry_id,
            "fileAsset[name]": "Project Data",
            "fileAsset[systemName]": "PROJECT_DATA",
            "fileAsset[fileExt]": "json",
        })
        assert "id" in added, f"Expected id in fileAsset.add response: {added}"
        file_asset_id = added["id"]
        state["file_asset_id"] = file_asset_id
        runner.register_cleanup(
            f"fileAsset {file_asset_id}",
            lambda: kaltura_post("fileAsset", "delete", {"id": file_asset_id}),
        )
        assert added.get("status") == 0, f"Expected status=0 (PENDING) before content, got {added.get('status')}"
        assert added.get("systemName") == "PROJECT_DATA", \
            f"Expected systemName=PROJECT_DATA, got {added.get('systemName')}"

        updated = kaltura_post("fileAsset", "setContent", {
            "id": file_asset_id,
            "contentResource[objectType]": "KalturaStringResource",
            "contentResource[content]": '{"hello":"world"}',
        })
        assert updated.get("status") == 2, f"Expected status=2 (READY) after setContent, got {updated.get('status')}"

        fetched = kaltura_post("fileAsset", "get", {"id": file_asset_id})
        assert fetched.get("objectId") == entry_id, f"Expected objectId={entry_id}, got {fetched.get('objectId')}"
        assert fetched.get("fileAssetObjectType") == 3, \
            f"Expected fileAssetObjectType=3, got {fetched.get('fileAssetObjectType')!r}"
        print(f"    fileAsset {file_asset_id}: status={fetched['status']}, objectId={fetched['objectId']}, systemName={fetched['systemName']}")

        listed = kaltura_post("fileAsset", "list", {
            "filter[objectType]": "KalturaFileAssetFilter",
            "filter[objectIdEqual]": entry_id,
            "filter[fileAssetObjectTypeEqual]": 3,
        })
        system_names = [o.get("systemName") for o in listed.get("objects", [])]
        assert "PROJECT_DATA" in system_names, f"Expected PROJECT_DATA in list, got {system_names}"
        print(f"    fileAsset.list found {listed['totalCount']} asset(s) for entry {entry_id}: {system_names}")

    runner.run_test("fileAsset.add/setContent/get/list — generic file attached to an entry", test_file_asset_lifecycle)

    def test_markdown_asset_lifecycle():
        """attachment_attachmentAsset.add/setContent/get/list with objectType=KalturaMarkdownAsset."""
        entry_id = state["model_entry_id"]

        added = kaltura_post("attachment_attachmentAsset", "add", {
            "attachmentAsset[objectType]": "KalturaMarkdownAsset",
            "attachmentAsset[filename]": "notes.md",
            "attachmentAsset[fileExt]": "md",
            "attachmentAsset[format]": 5,
            "entryId": entry_id,
        })
        assert added.get("objectType") == "KalturaMarkdownAsset", \
            f"Expected objectType=KalturaMarkdownAsset, got {added.get('objectType')}"
        asset_id = added["id"]
        state["markdown_asset_id"] = asset_id
        runner.register_cleanup(
            f"markdownAsset {asset_id}",
            lambda: kaltura_post("attachment_attachmentAsset", "delete", {"attachmentAssetId": asset_id}),
        )

        token = kaltura_post("uploadToken", "add", {})
        resp = requests.post(
            f"{SERVICE_URL}/service/uploadToken/action/upload",
            data={"ks": KS, "format": 1, "uploadTokenId": token["id"], "resume": False, "finalChunk": True},
            files={"fileData": ("notes.md", b"# Notes\n\nMarkdown asset lifecycle test.\n")},
            timeout=30,
        )
        resp.raise_for_status()

        updated = kaltura_post("attachment_attachmentAsset", "setContent", {
            "id": asset_id,
            "contentResource[objectType]": "KalturaUploadedFileTokenResource",
            "contentResource[token]": token["id"],
        })
        assert updated.get("status") == 2, f"Expected status=2 (READY) after setContent, got {updated.get('status')}"

        fetched = kaltura_post("attachment_attachmentAsset", "get", {"attachmentAssetId": asset_id})
        assert fetched.get("objectType") == "KalturaMarkdownAsset", \
            f"Expected objectType=KalturaMarkdownAsset on get, got {fetched.get('objectType')}"
        print(f"    markdownAsset {asset_id}: status={fetched['status']}, objectType={fetched['objectType']}")

        listed = kaltura_post("attachment_attachmentAsset", "list", {
            "filter[objectType]": "KalturaAssetFilter",
            "filter[entryIdEqual]": entry_id,
        })
        object_types = [o.get("objectType") for o in listed.get("objects", [])]
        assert "KalturaMarkdownAsset" in object_types, f"Expected KalturaMarkdownAsset in list, got {object_types}"
        print(f"    attachment_attachmentAsset.list found: {object_types}")

    runner.run_test(
        "attachment_attachmentAsset.add/setContent/get/list — KalturaMarkdownAsset",
        test_markdown_asset_lifecycle,
    )

    def test_knowledge_content_flow():
        """Document entry + document_documents.addContent + categoryEntry.add — the real content-add flow for a Knowledge base (Section 9.2)."""
        ts = int(time.time())
        cat = kaltura_post("category", "add", {"category[name]": f"CONTENT_MODEL_GUIDE_TEST_KB_{ts}"})
        cat_id = cat["id"]
        state["kb_category_id"] = cat_id
        runner.register_cleanup(f"category {cat_id}", lambda: kaltura_post("category", "delete", {"id": cat_id}))

        entry = kaltura_post("baseEntry", "add", {
            "entry[objectType]": "KalturaDocumentEntry",
            "entry[name]": f"CONTENT_MODEL_GUIDE_TEST_DOC_{ts}",
            "entry[type]": 10,
            "entry[documentType]": 11,
        })
        entry_id = entry["id"]
        state["kb_entry_id"] = entry_id
        runner.register_cleanup(f"entry {entry_id}", lambda: kaltura_post("baseEntry", "delete", {"entryId": entry_id}))
        assert entry.get("objectType") == "KalturaDocumentEntry", f"Expected KalturaDocumentEntry, got {entry.get('objectType')}"

        token = kaltura_post("uploadToken", "add", {})
        resp = requests.post(
            f"{SERVICE_URL}/service/uploadToken/action/upload",
            data={"ks": KS, "format": 1, "uploadTokenId": token["id"], "resume": False, "finalChunk": True},
            files={"fileData": ("doc.txt", b"knowledge base content-flow test document")},
            timeout=30,
        )
        resp.raise_for_status()

        content_added = kaltura_post("document_documents", "addContent", {
            "entryId": entry_id,
            "resource[objectType]": "KalturaUploadedFileTokenResource",
            "resource[token]": token["id"],
        })
        assert content_added.get("status") == 2, f"Expected status=2 (READY) after addContent, got {content_added.get('status')}"

        linked = kaltura_post("categoryEntry", "add", {
            "categoryEntry[objectType]": "KalturaCategoryEntry",
            "categoryEntry[categoryId]": cat_id,
            "categoryEntry[entryId]": entry_id,
        })
        assert linked.get("categoryId") == cat_id, f"Expected categoryId={cat_id}, got {linked.get('categoryId')}"
        assert linked.get("entryId") == entry_id, f"Expected entryId={entry_id}, got {linked.get('entryId')}"
        print(f"    Document entry {entry_id} (status={content_added['status']}) assigned to category {cat_id}")

    runner.run_test(
        "baseEntry.add + document_documents.addContent + categoryEntry.add — Knowledge base content flow",
        test_knowledge_content_flow,
    )

    # ════════════════════════════════════════════
    # Phase 2: eSearch Content-Gating
    # ════════════════════════════════════════════

    def test_no_content_entry_not_indexed():
        """A NO_CONTENT (status=7) entry is not found by eSearch, even by exact name."""
        entry_id = state["model_entry_id"]
        entry = kaltura_post("baseEntry", "get", {"entryId": entry_id})
        assert entry.get("status") == 7, f"Expected test entry to be NO_CONTENT, got {entry.get('status')}"

        result = esearch_post("searchEntry", {
            "searchParams[searchOperator][searchItems][0][objectType]": "KalturaESearchEntryItem",
            "searchParams[searchOperator][searchItems][0][searchTerm]": entry["name"],
            "searchParams[searchOperator][searchItems][0][itemType]": 1,  # EXACT_MATCH
            "searchParams[searchOperator][searchItems][0][fieldName]": "name",
            "searchParams[searchOperator][objectType]": "KalturaESearchEntryOperator",
            "searchParams[objectType]": "KalturaESearchEntryParams",
        })
        assert result["totalCount"] == 0, \
            f"Expected NO_CONTENT entry to be unindexed, got totalCount={result['totalCount']}"
        print(f"    NO_CONTENT entry '{entry['name']}' correctly absent from eSearch (totalCount=0)")

    runner.run_test("eSearch — NO_CONTENT entry is not indexed", test_no_content_entry_not_indexed)

    def test_ready_entry_found_immediately():
        """A READY (status=2) entry is found immediately by exact-name eSearch."""
        listing = kaltura_post("baseEntry", "list", {
            "filter[statusEqual]": 2,
            "pager[pageSize]": 1,
        })
        objects = listing.get("objects", [])
        assert objects, "Expected at least one READY entry in the account to validate eSearch findability"
        entry = objects[0]
        state["ready_entry_name"] = entry["name"]

        result = esearch_post("searchEntry", {
            "searchParams[searchOperator][searchItems][0][objectType]": "KalturaESearchEntryItem",
            "searchParams[searchOperator][searchItems][0][searchTerm]": entry["name"],
            "searchParams[searchOperator][searchItems][0][itemType]": 1,  # EXACT_MATCH
            "searchParams[searchOperator][searchItems][0][fieldName]": "name",
            "searchParams[searchOperator][objectType]": "KalturaESearchEntryOperator",
            "searchParams[objectType]": "KalturaESearchEntryParams",
        })
        assert result["totalCount"] > 0, \
            f"Expected READY entry '{entry['name']}' to be indexed, got totalCount=0"
        print(f"    READY entry '{entry['name']}' found by eSearch: totalCount={result['totalCount']}")

    runner.run_test("eSearch — READY entry is found immediately", test_ready_entry_found_immediately)

    # ════════════════════════════════════════════
    # Phase 3: AI Genie Knowledge Base Lifecycle
    # ════════════════════════════════════════════

    def test_knowledge_add_and_get():
        """/v1/knowledge/add creates a record; /v1/knowledge/get returns the documented schema."""
        ts = int(time.time())
        added = genie_post("/v1/knowledge/add", {
            "name": f"CONTENT_MODEL_GUIDE_TEST_{ts}",
            "config": {
                "sources": [
                    {
                        "type": "internal",
                        "language": "en",
                        "categoryIds": [],
                        "indexers": [
                            {"index_position": 0, "type": 1, "strategy": "EmbedCaptionV1"}
                        ],
                    }
                ]
            },
        })
        assert "id" in added, f"Expected id in add response: {added}"
        knowledge_id = added["id"]
        state["knowledge_id"] = knowledge_id
        runner.register_cleanup(
            f"knowledge record {knowledge_id}",
            lambda: genie_post("/v1/knowledge/delete", {"id": knowledge_id})
            if state.get("knowledge_deleted") is not True else None,
        )

        fetched = genie_post("/v1/knowledge/get", {"id": knowledge_id})
        assert fetched.get("id") == knowledge_id, f"Expected id={knowledge_id}, got {fetched.get('id')}"
        assert fetched.get("status") in ("READY", "DELETED"), \
            f"Expected status in (READY, DELETED), got {fetched.get('status')!r}"
        sources = fetched.get("config", {}).get("sources", [])
        assert sources and sources[0].get("type") == "internal", \
            f"Expected an internal source to round-trip, got {sources}"
        print(f"    Knowledge {knowledge_id}: status={fetched['status']!r} after add")

    runner.run_test("/v1/knowledge/add + get — schema and status field", test_knowledge_add_and_get)

    def test_knowledge_delete_lifecycle():
        """/v1/knowledge/delete removes the record; a subsequent get 404s."""
        knowledge_id = state["knowledge_id"]
        deleted = genie_post("/v1/knowledge/delete", {"id": knowledge_id})
        assert deleted is None or deleted == {}, f"Expected empty delete response, got {deleted}"
        state["knowledge_deleted"] = True

        try:
            genie_post("/v1/knowledge/get", {"id": knowledge_id})
            raise AssertionError("Expected get-after-delete to fail (record no longer exists)")
        except requests.exceptions.HTTPError as e:
            assert e.response.status_code == 404, \
                f"Expected 404 after delete, got {e.response.status_code}"
            print(f"    Knowledge {knowledge_id}: delete returned {deleted!r}, "
                  f"get-after-delete correctly 404s")

    runner.run_test("/v1/knowledge/delete — removes record, get-after-delete 404s", test_knowledge_delete_lifecycle)

    # ════════════════════════════════════════════
    # Cleanup & Summary
    # ════════════════════════════════════════════
    keep = "--keep" in sys.argv
    if keep:
        print(f"\n--keep: preserving resources. Entry: {state.get('model_entry_id')}")
    else:
        if sys.stdin.isatty():
            input("Press Enter to clean up...")
        runner.cleanup()

    success = runner.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    print(f"\n{'='*60}")
    print("  KALTURA CONTENT MODEL AND SEARCH GUIDE — E2E Validation")
    print(f"{'='*60}\n")
    main()
