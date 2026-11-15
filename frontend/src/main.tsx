import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import {
  api, ApiError, errorMessage, fields, groups, kindLabels, labels, masterLabels, reportLabels,
  roles, send, sources, stages, today,
} from './model'
import type { Doc, Masters, ReportResult, Row, User } from './model'
import './style.css'

function useData<T>(path: string, token: string, revision = 0, retain = false) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    if (!retain || !path) setData(null)
    setError('')
    if (path) api<T>(path, token, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setData(result)
    }).catch(caught => {
      if (!controller.signal.aborted) setError(errorMessage(caught))
    })
    return () => controller.abort()
  }, [path, token, revision, retain])
  return { data, error }
}

function Brand() {
  return <div className="brand"><span className="logo" aria-hidden="true"><i /><i /><i /></span>
    <span><strong>衡序 ERP</strong><small>HENGXU ERP</small></span></div>
}

function Notice({ children }: { children: ReactNode }) {
  return children ? <div className="notice" role="alert">{children}</div> : null
}

function Login({ onLogin }: { onLogin: (token: string, user: User) => void }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget))
    setBusy(true)
    setError('')
    try {
      const result = await api<{ token: string; user: User }>('/auth/login', '', send('POST', values))
      onLogin(result.token, result.user)
    } catch (caught) { setError(errorMessage(caught)) }
    finally { setBusy(false) }
  }
  return <main className="login">
    <section className="login-brand"><Brand /><div className="login-art" aria-hidden="true"><i /><i /><i /><i /></div>
      <div><h1>衡序业务工作台</h1><p>采购、销售、仓储、生产与财务</p></div>
    </section>
    <section className="login-main"><form onSubmit={submit}>
      <h2>登录账套</h2><Notice>{error}</Notice>
      <label>账套编码<input name="company" required defaultValue="DEMO" /></label>
      <label>账号<input name="username" required autoComplete="username" /></label>
      <label>密码<input name="password" type="password" required autoComplete="current-password" /></label>
      <button className="primary" disabled={busy}>{busy ? '登录中…' : '进入工作台'}</button>
    </form></section>
  </main>
}

type Open = (id: string) => void
type Period = { code: string; closed: boolean }
type PendingAction = { command: string; body: { version: number; operation_id: string; reason?: string; date?: string } }

function Status({ value }: { value: string }) {
  return <span className={'badge ' + value}><i />{stages[value] ?? value}</span>
}

function Empty({ children = '暂无记录' }: { children?: ReactNode }) {
  return <div className="empty">{children}</div>
}

function Docs({ docs, open }: { docs: Doc[]; open: Open }) {
  if (!docs.length) return <Empty />
  return <div className="table-wrap"><table><thead><tr><th>单据编号</th><th>业务类型</th><th>业务日期</th>
    <th>行数</th><th>状态</th><th>备注</th></tr></thead><tbody>{docs.map(doc => <tr key={doc.id}>
      <td><button className="link" onClick={() => open(doc.id)}>{doc.number}</button></td>
      <td>{kindLabels[doc.kind]}</td><td>{doc.date}</td><td>{doc.lines.length}</td>
      <td><Status value={doc.status} /></td><td>{doc.note || '—'}</td>
    </tr>)}</tbody></table></div>
}

function Value({ name, value, open }: { name: string; value: unknown; open: Open }) {
  const link = ['document', 'source', 'work_order', 'completion'].includes(name)
  if (link && typeof value === 'string') return <button className="link" title={value} onClick={() => open(value)}>{value.slice(0, 8).toUpperCase()} ↗</button>
  if (Array.isArray(value)) {
    return <details><summary>查看明细（{value.length}）</summary>
      {value.every(entry => typeof entry === 'string')
        ? value.map(entry => <button className="link" key={entry} onClick={() => open(entry)}>{entry.slice(0, 8)} ↗</button>)
        : <DataTable rows={value} open={open} />}</details>
  }
  if (value && typeof value === 'object') return <details><summary>查看明细</summary><dl>
    {Object.entries(value).map(([key, item]) => <div key={key}><dt>{labels[key] ?? key}</dt>
      <dd><Value name={key} value={item} open={open} /></dd></div>)}</dl></details>
  if (name === 'status') return <Status value={String(value)} />
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

function DataTable({ rows, open }: { rows: Record<string, unknown>[]; open: Open }) {
  if (!rows.length) return <Empty />
  const columns = [...new Set(rows.flatMap(row => Object.keys(row)))]
  return <div className="table-wrap"><table><thead><tr>{columns.map(column =>
    <th key={column}>{labels[column] ?? column}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}>{columns.map(column =>
      <td key={column}><Value name={column} value={row[column]} open={open} /></td>)}</tr>)}</tbody></table></div>
}

function Dashboard({ token, open, go }: { token: string; open: Open; go: (page: string) => void }) {
  const docs = useData<Doc[]>('/documents?limit=200', token)
  const stock = useData<ReportResult>('/reports/inventory?limit=200', token)
  const recent = docs.data?.slice(0, 7) ?? []
  const statusList = ['draft', 'review', 'ready', 'confirmed', 'posted']
  const counts = statusList.map(status => docs.data?.filter(doc => doc.status === status).length ?? 0)
  const max = Math.max(1, ...counts)
  return <>
    <section className="hero"><div><p className="eyebrow">运营总览</p><h1>业务工作台</h1>
      <p>查看库存余额、单据待办与过账进度。</p></div>
      <button onClick={() => go('reports')}>查看报表 ↗</button></section>
    <Notice>{docs.error || stock.error}</Notice>
    <div className="metrics">{[
      ['近期单据', docs.data?.length, '▤', '最近最多 200 张'],
      ['待审批', docs.data ? counts[1] : undefined, '◷', '近期单据中的待审批项'],
      ['待确认', docs.data ? counts[2] : undefined, '✓', '近期单据中的待确认项'],
      ['库存余额记录', stock.data?.rows.length, '▦', '按仓库和物料，最多 200 条'],
    ].map(([label, number, icon, note]) => <article className="metric" key={label}>
      <div><span>{label}</span><span className="metric-icon" aria-hidden="true">{icon}</span></div>
      <strong>{number ?? '—'}</strong><small>{note}</small>
    </article>)}</div>
    <div className="dashboard-grid">
      <section className="panel"><header><h2>最近单据</h2></header>{docs.data
        ? <Docs docs={recent} open={open} /> : <Empty>{docs.error ? '读取失败' : '加载中…'}</Empty>}</section>
      <section className="panel"><header><h2>近期单据状态</h2></header><div className="chart">
        {statusList.map((status, i) => <div key={status}><span>{stages[status]}</span>
          <span className="track"><i style={{ width: 100 * counts[i] / max + '%' }} /></span>
          <strong>{docs.data ? counts[i] : '—'}</strong></div>)}</div></section>
    </div>
  </>
}

function Pager({ page, setPage, more }: { page: number; setPage: (page: number) => void; more: boolean }) {
  return <footer className="pager"><button disabled={!page} onClick={() => setPage(page - 1)}>上一页</button>
    <span>第 {page + 1} 页</span><button disabled={!more} onClick={() => setPage(page + 1)}>下一页</button></footer>
}

function DocumentPage({ kinds, token, masters, open, create, revision }: {
  kinds: string[]; token: string; masters: Masters; open: Open; create: (kind: string) => void; revision: number
}) {
  const [kind, setKind] = useState(kinds[0])
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(0)
  const result = useData<Doc[]>('/documents?' + new URLSearchParams({
    kind, status, offset: String(page * 25), limit: '25',
  }), token, revision)
  return <section className="panel">
    <div className="tabs">{kinds.map(value => <button className={kind === value ? 'active' : ''} key={value}
      onClick={() => { setKind(value); setPage(0) }}>{kindLabels[value]}</button>)}</div>
    <header><h2>{kindLabels[kind]}</h2><div className="controls">
      <select aria-label="单据状态" value={status} onChange={event => { setStatus(event.target.value); setPage(0) }}>
        <option value="">全部状态</option>{Object.entries(stages).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
      {masters.writable.includes(kind) && <button className="primary" onClick={() => create(kind)}>＋ 新建单据</button>}
    </div></header>
    <Notice>{result.error}</Notice>{result.data ? <Docs docs={result.data} open={open} />
      : <Empty>{result.error ? '读取失败' : '加载中…'}</Empty>}
    <Pager page={page} setPage={setPage} more={result.data?.length === 25} />
  </section>
}

function Modal({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  const element = useRef<HTMLDialogElement>(null)
  useEffect(() => { element.current?.showModal() }, [])
  return <dialog ref={element} className="modal" aria-label={title} onCancel={close}>
    <header><h2>{title}</h2><button aria-label="关闭" onClick={close}>×</button></header>
    <div className="modal-content">{children}</div>
  </dialog>
}

function Source({ token, kind, row, change }: { token: string; kind: string; row: Row; change: (row: Row) => void }) {
  const result = useData<Doc[]>('/source-lines?kinds=' + (sources[kind] ?? []).join(','), token)
  const options = result.data ?? []
  const known = options.find(doc => doc.id === row.source)
  const manual = row.source && !known
  return <div className="source-field"><label>来源单据<select aria-label="来源单据" required value={row.source || ''} onChange={event =>
    change({ ...row, source: event.target.value, source_line: '' })}>
    <option value="">请选择来源单据</option>{options.map(doc =>
      <option key={doc.id} value={doc.id}>{kindLabels[doc.kind]} · {doc.number} · {doc.date}</option>)}
    {manual && <option value={row.source}>{row.source}</option>}
  </select></label><label>来源行{known ? <select aria-label="来源行" value={row.source_line || ''} required onChange={event =>
    change({ ...row, source_line: event.target.value })}><option value="">请选择来源行</option>
    {known.lines.map((line, index) => <option key={String(line.id)} value={String(line.id)}>
      第 {index + 1} 行 · {String(line.item ?? line.partner ?? '')} {String(line.qty ?? line.amount ?? '')}
    </option>)}</select> : <input required value={row.source_line || ''} placeholder="输入来源行 ID"
      onChange={event => change({ ...row, source_line: event.target.value })} />}</label>
    <details><summary>输入完整来源单据 ID</summary><input aria-label="完整来源单据 ID"
      value={row.source || ''} onChange={event => change({ ...row, source: event.target.value, source_line: '' })} /></details>
    <Notice>{result.error}</Notice>
  </div>
}

function Editor({ kind, doc, token, masters, saved }: {
  kind: string; doc?: Doc; token: string; masters: Masters; saved: (doc: Doc) => void
}) {
  const [rows, setRows] = useState<Row[]>(() => doc ? doc.lines as Row[] : [{}])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  function update(index: number, row: Row) { setRows(previous => previous.map((value, i) => index === i ? row : value)) }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget))
    const data = {
      ...values, ...(doc && { version: doc.version }), lines: rows.map(row => Object.fromEntries(
        Object.entries(row).filter(([key, value]) => value.trim() && (kind !== 'opening' ||
          (row.category === 'stock' ? key !== 'partner' : !['item', 'warehouse', 'qty'].includes(key)))),
      )),
    }
    setError('')
    setBusy(true)
    try { saved(await api<Doc>('/documents/' + (doc?.id || kind), token, send(doc ? 'PATCH' : 'POST', data))) }
    catch (caught) { setError(errorMessage(caught)) }
    finally { setBusy(false) }
  }
  return <form className="editor" onSubmit={save}><Notice>{error}</Notice>
    <div className="form-grid"><label>业务日期<input name="date" type="date" required defaultValue={doc?.date ?? today()} /></label>
      {kind === 'opening' && <label>期初权益<input name="equity" required inputMode="decimal" defaultValue={doc?.equity} /></label>}
      <label>备注<input name="note" maxLength={240} defaultValue={doc?.note} /></label></div>
    {rows.map((row, index) => <fieldset className="form-grid" key={index}><legend>第 {index + 1} 行</legend>
      {fields[kind].filter(field => kind !== 'opening' || (row.category === 'stock' ? field !== 'partner'
        : row.category ? !['item', 'warehouse', 'qty'].includes(field) : field === 'category')).map(field => {
        if (field === 'source') return <Source key={field} kind={kind} row={row} token={token} change={next => update(index, next)} />
        const type = ['item', 'component'].includes(field) ? 'item' : field.includes('warehouse') ? 'warehouse'
          : field === 'partner' ? (kind === 'sales_order' || row.category === 'AR' ? 'customer' : 'supplier') : ''
        const options = field === 'category' ? (kind === 'opening' ? [['stock', '库存'], ['AR', '应收'], ['AP', '应付']]
          : [['labor', '人工'], ['overhead', '制造费用']])
          : masters.records.filter(record => record.kind === type).map(record => [record.code, record.name + ' · ' + record.code])
        const optional = field === 'price' && ['purchase_invoice', 'sales_invoice', 'count', 'completion'].includes(kind)
        const change = (value: string) => update(index, { ...row, [field]: value })
        return <label key={field}>{labels[field]}{optional ? '（选填）' : ''}
          {type || field === 'category' ? <select aria-label={labels[field]} required value={row[field] || ''}
            onChange={event => change(event.target.value)}>
            <option value="">请选择</option>{options.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select> : <input required={!optional} inputMode="decimal" value={row[field] || ''} onChange={event => change(event.target.value)} />}</label>
      })}
      {rows.length > 1 && <button type="button" className="link" onClick={() => setRows(rows.filter((_, i) => i !== index))}>删除本行</button>}
    </fieldset>)}
    <div className="controls"><button type="button" disabled={rows.length >= 200} onClick={() => setRows([...rows, {}])}>＋ 添加一行</button>
      <button className="primary" disabled={busy}>{busy ? '保存中…' : '保存草稿'}</button></div>
  </form>
}


function Detail({ id, token, user, masters, open, edit, refresh }: {
  id: string; token: string; user: User; masters: Masters; open: Open; edit: (doc: Doc) => void; refresh: () => void
}) {
  const [revision, setRevision] = useState(0)
  const { data: doc, error: loadError } = useData<Doc>('/documents/' + id, token, revision)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState('')
  const pendingKey = ['hengxu-action', user.company, user.username, id].join('/')
  const [pending, setPendingState] = useState<PendingAction | null>(() => {
    const value = sessionStorage.getItem(pendingKey)
    return value ? JSON.parse(value) as PendingAction : null
  })
  function setPending(value: PendingAction | null) {
    if (value) sessionStorage.setItem(pendingKey, JSON.stringify(value))
    else sessionStorage.removeItem(pendingKey)
    setPendingState(value)
  }
  if (!doc) return <><Notice>{loadError}</Notice><Empty>{loadError ? '读取失败' : '加载中…'}</Empty></>
  const write = masters.writable.includes(doc.kind)
  const approve = ['admin', 'supervisor'].includes(user.role) && doc.status === 'review'
  const reverse = ['admin', 'finance'].includes(user.role) && ['confirmed', 'posted'].includes(doc.status)
    && !['opening', 'reversal', 'work_settlement'].includes(doc.kind)
  async function execute(command: string, fields: Row = {}, check = false) {
    if (!doc) return
    const previous = pending?.command === command ? pending.body : null
    const body = previous ?? { version: doc.version, operation_id: crypto.randomUUID(), ...fields }
    setBusy(true)
    setPending({ command, body })
    setError('')
    try {
      const result = check
        ? await api<{ document: Doc }>('/operations/' + body.operation_id, token)
        : await api<{ document: Doc }>('/documents/' + id + '/actions/' + command, token, send('POST', body))
      setPending(null)
      setMode('')
      setMessage('操作已提交 · ' + body.operation_id)
      setRevision(value => value + 1)
      refresh()
      if (result.document.id !== id) open(result.document.id)
    } catch (caught) {
      setError(errorMessage(caught))
      if (caught instanceof ApiError && caught.status < 500 && !check) setPending(null)
    } finally { setBusy(false) }
  }
  return <>
    <div className="document-meta"><Status value={doc.status} /><span>{doc.date}</span><span>版本 {doc.version}</span></div>
    <p className="document-id">单据 ID <code>{doc.id}</code></p>
    {doc.note && <p>{doc.note}</p>}
    {doc.reverse_of && <p>原始单据 <Value name="document" value={doc.reverse_of} open={open} /></p>}
    {doc.equity && <p>期初权益 {doc.equity}</p>}
    <DataTable rows={doc.lines} open={open} />
    {!!doc.progress?.length && <><h3>业务进度</h3><DataTable rows={doc.progress} open={open} /></>}
    {!!doc.allocations?.length && <><h3>下游单据与来源分配</h3><DataTable rows={doc.allocations} open={open} /></>}
    <Notice>{error}</Notice>{message && <p role="status">{message}</p>}
    <div className="controls document-actions">
      {write && doc.status === 'draft' && <><button disabled={busy || !!pending} onClick={() => edit(doc)}>编辑草稿</button>
        <button disabled={busy || !!pending} className="primary" onClick={() => execute('submit')}>提交</button></>}
      {approve && <><button disabled={busy || !!pending} onClick={() => setMode('reject')}>退回</button>
        <button disabled={busy || !!pending} className="primary" onClick={() => execute('approve')}>批准</button></>}
      {write && doc.status === 'ready' && <button disabled={busy || !!pending} className="primary" onClick={() => execute('confirm')}>业务确认</button>}
      {reverse && <button disabled={busy || !!pending} onClick={() => setMode('reverse')}>冲销</button>}
      {pending && <><button disabled={busy} onClick={() => execute(pending.command, {}, true)}>查询操作结果</button>
        <button disabled={busy} onClick={() => execute(pending.command)}>使用原操作编号重试</button></>}
    </div>
    {mode && <form className="form-grid" onSubmit={event => {
      event.preventDefault()
      const values = Object.fromEntries(new FormData(event.currentTarget)) as Row
      void execute(mode, values)
    }}><label>{mode === 'reverse' ? '冲销原因' : '退回原因'}<input name="reason" maxLength={240} required /></label>
      {mode === 'reverse' && <label>冲销日期<input name="date" type="date" defaultValue={today()} required /></label>}
      <button className="primary" disabled={busy || !!pending}>确认{mode === 'reverse' ? '冲销' : '退回'}</button>
    </form>}
  </>
}

function Reports({ token, open, user, masters }: { token: string; open: Open; user: User; masters: Masters }) {
  const [type, setType] = useState('inventory')
  const [cutoff, setCutoff] = useState('')
  const [warehouse, setWarehouse] = useState('')
  const [item, setItem] = useState('')
  const [page, setPage] = useState(0)
  const warehouseReport = ['inventory', 'movements'].includes(type)
  const finance = ['admin', 'supervisor', 'finance', 'cashier'].includes(user.role) && user.warehouses.includes('*')
  const params = new URLSearchParams({ offset: String(page * 25), limit: '25' })
  if (cutoff) params.set('cutoff', cutoff)
  if (warehouseReport && warehouse) params.set('warehouse', warehouse)
  if (warehouseReport && item) params.set('item', item)
  const result = useData<ReportResult>('/reports/' + type + '?' + params, token)
  return <section className="panel">
    <div className="tabs">{Object.entries(reportLabels).filter(([key]) => finance || ['inventory', 'movements'].includes(key))
      .map(([key, label]) => <button className={type === key ? 'active' : ''} onClick={() => { setType(key); setPage(0) }} key={key}>{label}</button>)}</div>
    <header><h2>{reportLabels[type]}</h2><div className="controls">
      <label>截止日期<input type="date" value={cutoff} onChange={event => { setCutoff(event.target.value); setPage(0) }} /></label>
      {warehouseReport && <><select aria-label="仓库筛选" value={warehouse} onChange={event => { setWarehouse(event.target.value); setPage(0) }}>
        <option value="">全部仓库</option>{masters.records.filter(record => record.kind === 'warehouse').map(record =>
          <option value={record.code} key={record.code}>{record.name}</option>)}</select>
        <select aria-label="物料筛选" value={item} onChange={event => { setItem(event.target.value); setPage(0) }}>
          <option value="">全部物料</option>{masters.records.filter(record => record.kind === 'item').map(record =>
            <option key={record.code} value={record.code}>{record.name}</option>)}</select></>}
    </div></header>
    <Notice>{result.error}</Notice>
    {result.data ? <><DataTable rows={result.data.rows} open={open} />
      <p className="snapshot">读取时间 {new Date(result.data.snapshot).toLocaleString('zh-CN')}</p></>
      : <Empty>{result.error ? '读取失败' : '加载中…'}</Empty>}
    <Pager page={page} setPage={setPage} more={result.data?.rows.length === 25} />
  </section>
}

function Settings({ token, user, masters, open, refresh }: {
  token: string; user: User; masters: Masters; open: Open; refresh: () => void
}) {
  const [version, setVersion] = useState(0)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const periods = useData<Period[]>('/periods', token, version)
  const audit = useData<Record<string, unknown>[]>(user.role === 'admin' ? '/audit' : '', token, version)
  const canClose = ['admin', 'finance'].includes(user.role) && user.warehouses.includes('*')
  async function submit(event: FormEvent<HTMLFormElement>, type: string) {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget)) as Row
    setError('')
    setMessage('')
    try {
      if (type === 'master') await api('/masters/' + values.kind + '/' + encodeURIComponent(values.code), token,
        send('PUT', { name: values.name, unit: values.unit }))
      else if (type === 'user') await api('/users/' + encodeURIComponent(values.username), token, send('PUT', {
        name: values.name, role: values.role, password: values.password,
        warehouses: values.warehouses.split(',').map(value => value.trim()).filter(Boolean),
      }))
      else await api('/periods/' + encodeURIComponent(values.code) + '/' + values.action, token, { method: 'POST' })
      setMessage('已保存')
      setVersion(value => value + 1)
      refresh()
    } catch (caught) { setError(errorMessage(caught)) }
  }
  return <div className="settings">
    <Notice>{error || periods.error || audit.error}</Notice>{message && <p role="status">{message}</p>}
    <section className="panel"><header><h2>会计期间</h2></header>
      <DataTable rows={(periods.data ?? []).map(period => ({ '期间': period.code, '期间状态': period.closed ? '已关闭' : '开放' }))} open={open} />
      {canClose && <form className="inline-form" onSubmit={event => submit(event, 'period')}>
        <label>期间<input type="month" name="code" required /></label>
        <label>动作<select name="action"><option value="open">开启新期间</option><option value="close">关闭期间</option></select></label>
        <button className="primary">执行</button></form>}
    </section>
    <section className="panel"><header><h2>基础档案</h2></header>
      <DataTable rows={masters.records.map(record => ({ '档案类别': masterLabels[record.kind], '编码': record.code,
        name: record.name, '基础单位': record.unit }))} open={open} />
      {user.role === 'admin' && <form className="inline-form" onSubmit={event => submit(event, 'master')}>
        <label>档案类别<select name="kind">{Object.entries(masterLabels).map(([key, name]) =>
          <option key={key} value={key}>{name}</option>)}</select></label>
        <label>编码<input name="code" required /></label><label>名称<input name="name" required /></label>
        <label>基础单位<input name="unit" /></label><button className="primary">保存档案</button></form>}
    </section>
    {user.role === 'admin' && <>
      <section className="panel"><header><h2>账号与岗位</h2></header>
        <form className="inline-form" onSubmit={event => submit(event, 'user')}>
          <label>账号<input name="username" required /></label><label>显示名称<input name="name" required /></label>
          <label>岗位<select name="role">{Object.entries(roles).map(([key, name]) => <option value={key} key={key}>{name}</option>)}</select></label>
          <label>仓库权限<input name="warehouses" placeholder="MAIN,AUX 或 *" /></label>
          <label>新密码<input name="password" type="password" minLength={12} required autoComplete="new-password" /></label>
          <button className="primary">保存账号</button></form></section>
      <section className="panel"><header><h2>审计记录</h2></header>
        <DataTable rows={(audit.data ?? []).map(row => ({ '操作人': row.actor, '动作': row.action,
          '对象': row.target, '结果': row.result, '原因': row.reason, '时间': row.timestamp }))} open={open} /></section>
    </>}
  </div>
}

function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem('hengxu-token') ?? '')
  const [user, setUser] = useState<User | null>(null)
  const [page, setPage] = useState('home')
  const [revision, setRevision] = useState(0)
  const [active, setActive] = useState('')
  const [editor, setEditor] = useState<{ kind: string; doc?: Doc } | null>(null)
  const [accountError, setAccountError] = useState('')
  const masters = useData<Masters>(token ? '/masters' : '', token, revision, true)
  useEffect(() => {
    if (!token) return
    const controller = new AbortController()
    api<User>('/auth/me', token, { signal: controller.signal }).then(setUser).catch(error => {
      if (controller.signal.aborted) return
      if (error instanceof ApiError && error.status === 401) {
        sessionStorage.removeItem('hengxu-token')
        setToken('')
      } else setAccountError(errorMessage(error))
    })
    return () => controller.abort()
  }, [token])
  function refresh() { setRevision(value => value + 1) }
  function open(id: string) { setActive(id); setEditor(null) }
  async function logout() {
    try { await api('/auth/logout', token, { method: 'POST' }) }
    finally {
      sessionStorage.removeItem('hengxu-token')
      setToken(''); setUser(null); setPage('home'); setActive(''); setEditor(null)
    }
  }
  if (!token) return <Login onLogin={(value, account) => {
    sessionStorage.setItem('hengxu-token', value); setUser(account); setToken(value); setAccountError('')
  }} />
  if (!user) return <main className="loading"><Notice>{accountError}</Notice>{accountError
    ? <button onClick={() => void logout()}>返回登录</button> : '正在恢复登录…'}</main>
  const group = groups.find(entry => entry.id === page) ?? groups[0]
  const data = masters.data
  return <div className="shell">
    <aside className="sidebar"><Brand /><nav aria-label="主导航">
      {groups.filter(entry => entry.id !== 'manufacturing' || data?.document_kinds.includes('bom'))
        .map(entry => <button key={entry.id} className={page === entry.id ? 'active' : ''}
          onClick={() => setPage(entry.id)}><span aria-hidden="true">{entry.icon}</span>{entry.label}</button>)}
    </nav><div className="sidebar-foot"><span className="avatar">{user.name.slice(0, 1)}</span>
      <div><strong>{user.name}</strong><small>{roles[user.role]}</small></div></div></aside>
    <div className="workspace"><header className="topbar"><span>工作空间 <i>/</i> {group.label}</span>
      <div><span>{data?.company ?? user.company}</span><button onClick={() => void logout()}>退出</button></div></header>
      <main className="content">
        {page !== 'home' && <div className="page-title"><h1>{group.label}</h1></div>}
        <Notice>{masters.error}</Notice>
        {page === 'home' && <Dashboard key={revision} token={token} open={open} go={setPage} />}
        {page !== 'home' && !data && <Empty>{masters.error ? '账套读取失败' : '正在加载账套…'}</Empty>}
        {data && group.kinds.length > 0 && <DocumentPage key={page} token={token} kinds={group.kinds}
          masters={data} open={open} create={kind => setEditor({ kind })} revision={revision} />}
        {data && page === 'reports' && <Reports token={token} user={user} masters={data} open={open} />}
        {data && page === 'settings' && <Settings token={token} user={user} masters={data} open={open} refresh={refresh} />}
      </main>
    </div>
    {data && editor && <Modal title={(editor.doc ? '编辑' : '新建') + kindLabels[editor.kind]} close={() => setEditor(null)}>
      <Editor key={editor.doc?.id ?? editor.kind} {...editor} masters={data} token={token} saved={doc => { open(doc.id); refresh() }} />
    </Modal>}
    {data && active && !editor && <Modal title="单据详情" close={() => setActive('')}>
      <Detail key={active} id={active} token={token} user={user} masters={data} open={open}
        edit={doc => setEditor({ kind: doc.kind, doc })} refresh={refresh} />
    </Modal>}
  </div>
}

createRoot(document.getElementById('root')!).render(<App />)

