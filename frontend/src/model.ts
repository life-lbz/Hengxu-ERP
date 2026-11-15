export type User = { username: string; name: string; company: string; role: string; warehouses: string[] }
export type Master = { kind: string; code: string; name: string; unit: string }
export type Masters = { company: string; records: Master[]; document_kinds: string[]; writable: string[] }
export type Row = Record<string, string>
export type Allocation = { document: string; line: string; source_line: string; kind: string; qty: string; amount: string; status: string }
export type Doc = {
  id: string; number: string; kind: string; date: string; status: string; version: number
  warehouses: string[]; note: string; reverse_of: string | null; equity?: string
  lines: Record<string, unknown>[]; allocations?: Allocation[]; progress?: Record<string, unknown>[]
}
export type ReportResult = { rows: Record<string, unknown>[]; snapshot: string; cutoff: string | null }

export const stages: Record<string, string> = {
  draft: '草稿', review: '待审批', ready: '待确认', confirmed: '已确认', posted: '已过账', reversed: '已冲销',
}
export const kindLabels: Record<string, string> = {
  opening: '期初批次', purchase_order: '采购订单', receipt: '采购收货', purchase_return: '采购退货',
  purchase_invoice: '采购发票', payment: '付款核销', sales_order: '销售订单', shipment: '销售发货',
  sales_return: '销售退货', sales_invoice: '销售发票', collection: '收款核销',
  transfer: '库存调拨', count: '库存盘点', bom: '物料清单', work_order: '生产工单',
  issue: '生产领料', material_return: '生产退料', scrap: '领料报废', production_expense: '生产费用',
  completion: '完工入库', work_settlement: '工单结算', reversal: '冲销记录',
}
export const fields: Record<string, string[]> = {
  opening: ['category', 'item', 'warehouse', 'qty', 'partner', 'amount'],
  purchase_order: ['item', 'warehouse', 'partner', 'qty', 'price'],
  sales_order: ['item', 'warehouse', 'partner', 'qty', 'price'],
  receipt: ['source', 'qty'], shipment: ['source', 'qty'],
  purchase_return: ['source', 'qty'], sales_return: ['source', 'qty'],
  purchase_invoice: ['source', 'qty', 'price'], sales_invoice: ['source', 'qty', 'price'],
  payment: ['source', 'amount'], collection: ['source', 'amount'],
  transfer: ['item', 'warehouse', 'to_warehouse', 'qty'], count: ['item', 'warehouse', 'expected_qty', 'qty', 'price'],
  bom: ['item', 'component', 'qty'],
  work_order: ['source', 'item', 'warehouse', 'qty'], issue: ['source', 'item', 'warehouse', 'qty'],
  material_return: ['source', 'qty'], scrap: ['source', 'qty'], production_expense: ['source', 'category', 'amount'],
  completion: ['source', 'qty', 'price'], work_settlement: ['source'],
}
export const sources: Record<string, string[]> = {
  receipt: ['purchase_order'], purchase_return: ['receipt'], purchase_invoice: ['receipt'],
  payment: ['purchase_invoice', 'opening'], shipment: ['sales_order'], sales_return: ['shipment'],
  sales_invoice: ['shipment'], collection: ['sales_invoice', 'opening'], work_order: ['bom'],
  issue: ['work_order'], material_return: ['issue'], scrap: ['issue'], production_expense: ['work_order'],
  completion: ['work_order'], work_settlement: ['work_order'],
}
export const roles: Record<string, string> = {
  admin: '系统管理员', supervisor: '业务主管', buyer: '采购员', seller: '销售员', warehouse: '仓管员',
  finance: '会计', cashier: '出纳', production: '生产计划员',
}
export const groups = [
  { id: 'home', label: '工作台', icon: '⌂', kinds: [] },
  { id: 'purchases', label: '采购管理', icon: '◫', kinds: ['purchase_order', 'receipt', 'purchase_return', 'purchase_invoice', 'payment'] },
  { id: 'sales', label: '销售管理', icon: '↗', kinds: ['sales_order', 'shipment', 'sales_return', 'sales_invoice', 'collection'] },
  { id: 'inventory', label: '仓储库存', icon: '▦', kinds: ['transfer', 'count'] },
  { id: 'manufacturing', label: '生产制造', icon: '◎', kinds: ['bom', 'work_order', 'issue', 'material_return', 'scrap', 'production_expense', 'completion', 'work_settlement'] },
  { id: 'finance', label: '财务会计', icon: '▤', kinds: ['opening', 'reversal'] },
  { id: 'reports', label: '经营报表', icon: '▥', kinds: [] },
  { id: 'settings', label: '账套设置', icon: '⚙', kinds: [] },
]
export const labels: Record<string, string> = {
  id: '行号', item: '物料', component: '组件', warehouse: '仓库', to_warehouse: '调入仓库',
  partner: '往来单位', qty: '数量', expected_qty: '账面数量', price: '单价', amount: '金额',
  source: '来源单据', source_line: '来源行', category: '类别', on_hand: '现存量', reserved: '预留量',
  available: '可用量', document: '来源单据', date: '业务日期', line: '来源行', value: '成本金额',
  reservation: '预留增减', rule: '规则版本', entries: '分录', side: '往来方向', settled: '已核销',
  outstanding: '未结金额', age_days: '账龄（天）', account: '科目', name: '名称', debit: '借方余额',
  credit: '贷方余额', ledger: '总账', subledger: '子账', difference: '差额', stock_amount: '出入库成本',
  work_order: '对应工单', work_line: '工单行', unit_cost: '单位成本', adjustments: '成本差异明细',
  usage: '用量分析', components: 'BOM 快照', movement: '流水顺序', kind: '类型',
  actual: '实际耗用', variance: '用量差异', scrap: '报废数量', status: '状态',
  row: '行次', returned: '已退数量', invoiced: '已开票量', unbilled: '待开票量',
  completed: '已处理', remaining: '待处理',
}
export const reportLabels: Record<string, string> = {
  inventory: '库存余额', movements: '库存流水', valuation: '库存估值',
  journals: '凭证明细', 'open-items': '往来与账龄', 'trial-balance': '试算平衡', reconciliation: '账务对账',
}
export const masterLabels: Record<string, string> = {
  item: '物料', unit: '计量单位', warehouse: '仓库', supplier: '供应商', customer: '客户', cost_center: '成本中心',
}
export function today() {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}

export class ApiError extends Error {
  constructor(message: string, public code: string, public status: number) { super(message) }
}
export async function api<T>(path: string, token: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', 'Bearer ' + token)
  const response = await fetch('/api/v1' + path, { ...options, headers })
  const json = await response.json()
  if (!response.ok) throw new ApiError(
    [json.message || '请求失败', json.trace_id && '追踪号 ' + json.trace_id].filter(Boolean).join(' · '),
    json.code, response.status,
  )
  return json as T
}
export const send = (method: string, data: unknown): RequestInit => ({ method, body: JSON.stringify(data) })
export const errorMessage = (error: unknown) => error instanceof Error ? error.message : '请求失败'
