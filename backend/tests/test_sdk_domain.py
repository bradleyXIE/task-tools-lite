import json


def test_domain_tools(run):
    async def go(pool):
        from app.tasks import service as svc
        from app.tools import registry
        from app.tools.sdk import domain
        t = await svc.create_task(pool, "dom", "", "g", start=False)
        tid = int(t["id"])
        try:
            async def c(name, **a):
                return await registry.call_tool(pool, name, a, tid)

            r = await c("insert_assets", assets=[
                {"type": "subdomain", "domain": "a.example.com"},
                {"type": "ip", "ip": "1.2.3.4", "open_ports": [{"port": 80}]},
                {"type": "ip", "ip": "not-an-ip"},
                {"type": "service", "url": "https://a.example.com:8443", "status_code": 403, "technologies": ["Nginx"]},
                {"type": "service", "service_name": "ssh", "port": 22, "ip": "1.2.3.4"},
                {"type": "endpoint", "url": "https://a.example.com/x", "method": "post"}])
            assert r["ok"], r
            out = r["output"]
            assert len(out["results"]) == 5 and out["errors"][0]["index"] == 2
            ids = [x["id"] for x in out["results"]]
            # merge on duplicate
            r = await c("insert_assets", assets=[{"type": "service", "url": "https://a.example.com:8443", "technologies": ["Vue"]}])
            assert r["output"]["results"][0]["id"] == ids[2]
            r = await c("list_assets", dsl="status_code>=400 AND technology=vue")
            assert r["output"]["count"] == 1
            r = await c("list_assets", dsl="(port==22 OR port==80) AND type_x=1")
            assert r["ok"]
            r = await c("list_assets", dsl="port==22")
            assert r["output"]["count"] == 1
            assert (await c("list_assets", dsl="", type="ip"))["ok"] is False
            assert (await c("list_assets", ids=ids[:2]))["output"]["count"] == 2

            r = await c("list_untested_assets", type="ip")
            assert r["output"]["total"] == 1
            r = await c("record_fact", summary="found stuff", detail="d", evidence="e", confidence="observed", asset_ids=[ids[1]])
            assert r["output"].startswith("fact recorded: ")
            fid = int(r["output"].split(": ")[1])
            assert (await c("list_untested_assets", type="ip"))["output"]["total"] == 0
            r = await c("record_fact", facts=[{"summary": "one"}, {"summary": ""}])
            assert r["output"]["ids"][1] == 0 and "1" in r["output"]["errors"]
            r = await c("list_facts", limit=1)
            assert r["output"]["total"] == 2 and r["output"]["has_more"] and r["output"]["next_before"]
            r2 = await c("list_facts", limit=5, before=r["output"]["next_before"])
            assert len(r2["output"]["facts"]) == 1
            assert (await c("list_facts", q="stuff"))["output"]["total"] == 1
            nd = await c("node_detail", id=fid, kind="fact")
            assert nd["output"]["detail"].endswith("Evidence: e")

            r = await c("report_finding", vulnclass="sqli", severity="high", summary="s", evidence="ev", asset_ids=[ids[0]])
            assert r["output"].startswith("finding recorded: ")
            nid = int(r["output"].split("\n")[0].split(": ")[1])
            assert (await c("report_finding", vulnclass="x", severity="bogus", summary="s"))["ok"] is False
            assert (await c("update_finding_report", finding_id=str(nid), report="# R"))["output"] == f"finding {nid} report updated (3 chars)"
            g = await c("get_finding_record", finding_id=nid)
            assert g["output"]["finding"]["report"] == "# R"
            assert (await c("list_findings"))["output"][0]["severity"] == "high"
            assert (await c("list_task_findings", task_id=str(tid)))["output"][0]["report"] == "# R"
            assert (await c("node_detail", id=nid, kind="finding"))["output"]["kind"] == "finding"

            r = await c("add_company_scope", company="Acme", scope="example.com\n10.0.0.0/8\n1.2.3.0/24\nICP-12345678\nacme corp", reason="whois")
            o = r["output"]
            assert o["added"] == 4 and o["invalid"] == 1, o
            r = await c("add_task_scope", entries=[{"kind": "ip", "value": "9.9.9.9"}, {"kind": "bogus", "value": "x"}], reason="r")
            assert r["ok"], r
            assert len(r["output"]["added"]) == 1 and r["output"]["errors"]
            comp = (await c("list_companies", search="acm"))["output"]
            assert comp["count"] == 1 and len(comp["companies"][0]["scope"]) == 4

            r = await c("add_hint", hints=[{"text": "h1"}, {"text": "h2"}])
            assert len(r["output"]["ids"]) == 2
            assert (await c("add_hint", text="solo"))["output"].startswith("hint added")

            ov = (await c("graph_overview"))["output"]
            assert ov["assets"]["total"] == 5 and ov["coverage"]["tested"] == 2 and len(ov["hints"]) == 3
            assert ov["findings"]["by_severity"] == {"high": 1}
            assert (await c("get_task_graph", task_id=str(tid)))["output"]["task"]["id"] == tid
            assert (await c("list_llm_profiles"))["output"]["profiles"][0]["is_active"]
            assert "key" not in json.dumps((await c("list_llm_profiles"))["output"]).lower()

            lt = (await c("list_task_worker_traces", task_id=str(tid)))["output"]
            assert lt["steps"] > 0
            s = (await c("search_task_worker_traces", task_id=tid, q="sqli"))["output"]["matches"]
            assert s
            gt = (await c("get_task_worker_trace", task_id=tid, step_ids=[s[0]["step_id"]] * 6))["output"]
            assert gt["steps"] and len(gt["omitted_step_ids"]) == 1
            assert (await c("graph_overview"))["ok"]
            nt = await registry.call_tool(pool, "list_facts", {}, None)
            assert nt["ok"] is False
        finally:
            pass  # the autouse track_task fixture removes the task created above
    run(go)
