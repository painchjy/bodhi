INSERT INTO wiki_pages (id,tenant_id,knowledge_base_id,slug,title,page_type,status,content,summary,page_metadata,last_edit_source)
SELECT gen_random_uuid(),10000,$$a7f31b49-3906-4df8-bc46-096370615371$$,$$agent/causalpattern/V-CB-01$$,$$因果模式：熔断保护（V-CB-01）$$,$$agent:CausalPattern$$,$$published$$,$$# 因果模式：熔断保护（V-CB-01）

- patternKind: valid
- patternStatus: active
- 方案：下游调用增加超时、熔断、fallback 和资源隔离
- 机制：失败调用快速失败，避免线程/连接被占满；半开探测恢复
- 目标：抑制下游故障引发的级联资源耗尽
- 前提：允许降级；有 fallback；有超时和状态观测
- 限制：不修复下游根因；不保证消息送达

```json
{"id":"V-CB-01","kind":"valid","mechanism":"fail-fast + fallback + resource isolation","premises":["允许降级","有 fallback","有超时","状态可观测"],"limits":["不修复根因","不保证消息送达"]}
```$$,$$熔断、降级与资源隔离的因果模式$$,$${"ontology":{"class":"agent:CausalPattern","label":"因果模式"}}$$::jsonb,$$seed$$
WHERE NOT EXISTS (SELECT 1 FROM wiki_pages WHERE knowledge_base_id=$$a7f31b49-3906-4df8-bc46-096370615371$$ AND slug=$$agent/causalpattern/V-CB-01$$ AND deleted_at IS NULL);

INSERT INTO wiki_pages (id,tenant_id,knowledge_base_id,slug,title,page_type,status,content,summary,page_metadata,last_edit_source)
SELECT gen_random_uuid(),10000,$$a7f31b49-3906-4df8-bc46-096370615371$$,$$agent/causalpattern/V-CA-01$$,$$因果模式：缓存旁路（V-CA-01）$$,$$agent:CausalPattern$$,$$published$$,$$# 因果模式：缓存旁路（V-CA-01）

- patternKind: valid
- patternStatus: active
- 方案：读多写少数据使用 Cache-Aside，写后失效并设置 TTL
- 机制：缓存命中绕过数据库；写后失效控制陈旧窗口
- 目标：降低读延迟和后端读压力
- 前提：允许短时陈旧；有失效/TTL；缓存容量可接受
- 限制：不提供强一致；不适合不可陈旧数据

```json
{"id":"V-CA-01","kind":"valid","mechanism":"cache hit + write invalidation + TTL","premises":["读多写少","允许短时陈旧","有失效/TTL"],"limits":["不保证强一致"]}
```$$,$$缓存旁路的读性能因果模式$$,$${"ontology":{"class":"agent:CausalPattern","label":"因果模式"}}$$::jsonb,$$seed$$
WHERE NOT EXISTS (SELECT 1 FROM wiki_pages WHERE knowledge_base_id=$$a7f31b49-3906-4df8-bc46-096370615371$$ AND slug=$$agent/causalpattern/V-CA-01$$ AND deleted_at IS NULL);

INSERT INTO wiki_pages (id,tenant_id,knowledge_base_id,slug,title,page_type,status,content,summary,page_metadata,last_edit_source)
SELECT gen_random_uuid(),10000,$$a7f31b49-3906-4df8-bc46-096370615371$$,$$agent/causalpattern/V-SCOPE-01$$,$$因果模式：局部变量阻断跨作用域副作用（V-SCOPE-01）$$,$$agent:CausalPattern$$,$$published$$,$$# 因果模式：局部变量阻断跨作用域副作用（V-SCOPE-01）

- patternKind: valid
- patternStatus: active
- 方案：将共享全局状态改为当前事务作用域内的局部变量
- 机制：局部变量不跨作用域传播，错误写入不能影响其他交易
- 目标：阻断跨交易副作用，避免未签约扣款
- 前提：错误写入确实发生在共享全局状态；没有其他共享状态承载同一语义
- 限制：不解决共享数据库/缓存问题；其他共享状态仍可能传播

```json
{"id":"V-SCOPE-01","kind":"valid","mechanism":"scope isolation","premises":["错误写入发生在共享全局状态","局部变量不跨作用域","无其他共享状态"],"limits":["不解决共享数据库/缓存"]}
```$$,$$局部变量阻断跨作用域副作用的因果模式$$,$${"ontology":{"class":"agent:CausalPattern","label":"因果模式"}}$$::jsonb,$$seed$$
WHERE NOT EXISTS (SELECT 1 FROM wiki_pages WHERE knowledge_base_id=$$a7f31b49-3906-4df8-bc46-096370615371$$ AND slug=$$agent/causalpattern/V-SCOPE-01$$ AND deleted_at IS NULL);


INSERT INTO wiki_pages (id,tenant_id,knowledge_base_id,slug,title,page_type,status,content,summary,page_metadata,last_edit_source)
SELECT gen_random_uuid(),10000,$$a7f31b49-3906-4df8-bc46-096370615371$$,$$agent/causalpattern/I-CACHE-CONSISTENCY$$,$$错误因果模式：缓存保证强一致（I-CACHE-CONSISTENCY）$$,$$agent:CausalPattern$$,$$published$$,$$# 错误因果模式：缓存保证强一致（I-CACHE-CONSISTENCY）

- patternKind: invalid
- patternStatus: active
- 错误主张：引入缓存可以保证跨系统强一致
- 逻辑问题：缓存旁路只降低读压力并引入陈旧窗口，不提供一致性协议
- 结论：该主张不成立；需要强一致时必须说明写入、失效、版本/冲突处理

```json
{"id":"I-CACHE-CONSISTENCY","kind":"invalid","mechanism":"cache is not a consistency protocol"}
```$$,$$错误因果模式：缓存不能单独保证强一致$$,$${"ontology":{"class":"agent:CausalPattern","label":"因果模式"}}$$::jsonb,$$seed$$
WHERE NOT EXISTS (SELECT 1 FROM wiki_pages WHERE knowledge_base_id=$$a7f31b49-3906-4df8-bc46-096370615371$$ AND slug=$$agent/causalpattern/I-CACHE-CONSISTENCY$$ AND deleted_at IS NULL);

INSERT INTO wiki_pages (id,tenant_id,knowledge_base_id,slug,title,page_type,status,content,summary,page_metadata,last_edit_source)
SELECT gen_random_uuid(),10000,$$a7f31b49-3906-4df8-bc46-096370615371$$,$$agent/causalpattern/I-RETRY-CAPACITY$$,$$错误因果模式：重试解决容量不足（I-RETRY-CAPACITY）$$,$$agent:CausalPattern$$,$$published$$,$$# 错误因果模式：重试解决容量不足（I-RETRY-CAPACITY）

- patternKind: invalid
- patternStatus: active
- 错误主张：增加重试可以解决系统容量不足
- 逻辑问题：重试会放大流量和资源占用，不增加实际容量
- 结论：该主张不成立；容量问题需要扩容、限流或降载

```json
{"id":"I-RETRY-CAPACITY","kind":"invalid","mechanism":"retry amplifies load"}
```$$,$$错误因果模式：重试不是扩容手段$$,$${"ontology":{"class":"agent:CausalPattern","label":"因果模式"}}$$::jsonb,$$seed$$
WHERE NOT EXISTS (SELECT 1 FROM wiki_pages WHERE knowledge_base_id=$$a7f31b49-3906-4df8-bc46-096370615371$$ AND slug=$$agent/causalpattern/I-RETRY-CAPACITY$$ AND deleted_at IS NULL);


UPDATE wiki_pages
   SET page_metadata = jsonb_build_object(
         $$ontology$$, jsonb_build_object(
           $$name$$, title, $$class$$, page_type, $$model$$, $$agent$$, $$generator$$, $$seed$$,
           $$attributes$$, jsonb_build_object(
             $$patternKind$$, CASE WHEN slug LIKE $$agent/causalpattern/I-%$$ THEN $$invalid$$ ELSE $$valid$$ END,
             $$patternStatus$$, $$active$$, $$patternVersion$$, $$1$$, $$patternSource$$, $$tests/agent_e2e$$,
             $$patternDefinition$$, content, $$skos:definition$$, summary)))
 WHERE knowledge_base_id=$$a7f31b49-3906-4df8-bc46-096370615371$$
   AND slug LIKE $$agent/causalpattern/%$$ AND deleted_at IS NULL;

