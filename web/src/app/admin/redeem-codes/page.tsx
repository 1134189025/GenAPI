"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Copy, LoaderCircle, Pencil, Plus, RefreshCw, Search, Ticket, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { DataPanel } from "@/components/common/data-panel";
import { EmptyState } from "@/components/common/empty-state";
import { PageHeader } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { deleteRedeemCode, fetchAdminMembershipPlans, fetchRedeemCodes, generateRedeemCodes, updateRedeemCode, type MembershipPlan, type RedeemCode, type RedeemCodeType } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

import { datetimeLocalToApiValue, formatAdminDateTime, toDatetimeLocalValue } from "../promo-codes/components/promo-code-helpers";
import { copyTextToClipboard, getRedeemCodeStatus, getRedeemCodeTypeLabel, getRedeemValueLabel, type RedeemCodeStatusKey } from "./components/redeem-code-helpers";

type RedeemStatusFilter = "all" | RedeemCodeStatusKey;

type GenerateFormState = {
  type: RedeemCodeType;
  value: string;
  count: string;
  expires_at: string;
  membership_plan_id: string;
};

type EditFormState = {
  enabled: boolean;
  expires_at: string;
};

const defaultGenerateForm: GenerateFormState = {
  type: "image_quota",
  value: "10",
  count: "1",
  expires_at: "",
  membership_plan_id: "",
};

export default function AdminRedeemCodesPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  const didLoadRef = useRef(false);
  const [items, setItems] = useState<RedeemCode[]>([]);
  const [plans, setPlans] = useState<MembershipPlan[]>([]);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<RedeemCodeType | "all">("all");
  const [statusFilter, setStatusFilter] = useState<RedeemStatusFilter>("all");
  const [isLoading, setIsLoading] = useState(true);
  const [isGenerateOpen, setIsGenerateOpen] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [generateForm, setGenerateForm] = useState<GenerateFormState>(defaultGenerateForm);
  const [generatedCodes, setGeneratedCodes] = useState<string[]>([]);
  const [isResultOpen, setIsResultOpen] = useState(false);
  const [editingCode, setEditingCode] = useState<RedeemCode | null>(null);
  const [editForm, setEditForm] = useState<EditFormState>({ enabled: true, expires_at: "" });
  const [isUpdating, setIsUpdating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<RedeemCode | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [pendingId, setPendingId] = useState("");
  const generatedCodesTextareaRef = useRef<HTMLTextAreaElement>(null);
  const canLoadAdminData = !isCheckingAuth && session?.role === "admin";

  const load = async (silent = false) => {
    if (!silent) setIsLoading(true);
    try {
      const data = await fetchRedeemCodes();
      setItems(data.items);
      const planData = await fetchAdminMembershipPlans();
      setPlans(planData.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载兑换码失败");
    } finally {
      if (!silent) setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!canLoadAdminData || didLoadRef.current) return;
    didLoadRef.current = true;
    void load();
  }, [canLoadAdminData]);

  const filteredItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return items.filter((item) => {
      const status = getRedeemCodeStatus(item).key;
      const typeMatched = typeFilter === "all" || item.type === typeFilter;
      const statusMatched = statusFilter === "all" || status === statusFilter;
      const queryMatched =
        !normalizedQuery ||
        item.code_preview.toLowerCase().includes(normalizedQuery) ||
        String(item.used_by_user_id || "").toLowerCase().includes(normalizedQuery);
      return typeMatched && statusMatched && queryMatched;
    });
  }, [items, query, statusFilter, typeFilter]);

  const summary = useMemo(() => {
    return items.reduce(
      (acc, item) => {
        acc.total += 1;
        const status = getRedeemCodeStatus(item).key;
        if (status === "available") acc.available += 1;
        if (status === "used") acc.used += 1;
        if (status === "expired") acc.expired += 1;
        return acc;
      },
      { total: 0, available: 0, used: 0, expired: 0 },
    );
  }, [items]);

  const handleGenerate = async () => {
    if (generateForm.type === "membership" && !generateForm.membership_plan_id) {
      toast.error("请选择会员套餐");
      return;
    }
    setIsGenerating(true);
    try {
      const expiresAt = datetimeLocalToApiValue(generateForm.expires_at);
      const data = await generateRedeemCodes({
        type: generateForm.type,
        value: generateForm.type === "invitation" || generateForm.type === "membership" ? 0 : Math.max(1, Math.trunc(Number(generateForm.value) || 1)),
        count: Math.max(1, Math.trunc(Number(generateForm.count) || 1)),
        ...(expiresAt ? { expires_at: expiresAt } : {}),
        ...(generateForm.type === "membership" ? { membership_plan_id: generateForm.membership_plan_id } : {}),
      });
      setItems(data.items);
      setGeneratedCodes(data.codes.map((item) => String(item.code || "")).filter(Boolean));
      setIsGenerateOpen(false);
      setIsResultOpen(true);
      setGenerateForm(defaultGenerateForm);
      toast.success("兑换码已生成");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "生成兑换码失败");
    } finally {
      setIsGenerating(false);
    }
  };

  const copyGeneratedCodes = async () => {
    const text = generatedCodesTextareaRef.current?.value || generatedCodes.join("\n");
    if (await copyTextToClipboard(text, { sourceElement: generatedCodesTextareaRef.current })) {
      toast.success("已复制全部兑换码");
      return;
    }
    toast.error("复制失败，请手动选中明文兑换码复制");
  };

  const openEditDialog = (item: RedeemCode) => {
    setEditingCode(item);
    setEditForm({ enabled: item.enabled, expires_at: toDatetimeLocalValue(item.expires_at) });
  };

  const handleUpdate = async () => {
    if (!editingCode) return;
    setIsUpdating(true);
    setPendingId(editingCode.id);
    try {
      const expiresAt = datetimeLocalToApiValue(editForm.expires_at);
      const data = await updateRedeemCode(editingCode.id, {
        enabled: editForm.enabled,
        expires_at: expiresAt ?? "",
      });
      setItems(data.items);
      setEditingCode(null);
      toast.success("兑换码已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新兑换码失败");
    } finally {
      setIsUpdating(false);
      setPendingId("");
    }
  };

  const handleToggle = async (item: RedeemCode) => {
    setPendingId(item.id);
    try {
      const data = await updateRedeemCode(item.id, { enabled: !item.enabled });
      setItems(data.items);
      toast.success(item.enabled ? "兑换码已禁用" : "兑换码已启用");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新兑换码失败");
    } finally {
      setPendingId("");
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    setPendingId(deleteTarget.id);
    try {
      const data = await deleteRedeemCode(deleteTarget.id);
      setItems(data.items);
      setDeleteTarget(null);
      toast.success("兑换码已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除兑换码失败");
    } finally {
      setIsDeleting(false);
      setPendingId("");
    }
  };

  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return (
    <section className="space-y-6">
      <PageHeader
        eyebrow="Redeem Codes"
        title="兑换码管理"
        description="生成、筛选和维护 GGB 兑换码、图片并发与邀请兑换码。明文兑换码只在生成后展示一次。"
        actions={
          <>
            <Button variant="outline" className="h-10 rounded-xl border-stone-200 bg-white/85" disabled={isLoading} onClick={() => void load()}>
              <RefreshCw className={cn("size-4", isLoading ? "animate-spin" : "")} />
              刷新
            </Button>
            <Button className="h-10 rounded-xl bg-slate-950 text-white hover:bg-slate-800" onClick={() => setIsGenerateOpen(true)}>
              <Plus className="size-4" />
              生成兑换码
            </Button>
          </>
        }
      />

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="兑换码总数" value={summary.total} icon={<Ticket className="size-5" />} tone="slate" />
        <StatCard label="可用" value={summary.available} icon={<Ticket className="size-5" />} tone="emerald" />
        <StatCard label="已使用" value={summary.used} icon={<Ticket className="size-5" />} tone="blue" />
        <StatCard label="已过期" value={summary.expired} icon={<Ticket className="size-5" />} tone="rose" />
      </div>

      <DataPanel
        title="兑换码列表"
        description="列表仅展示预览码；完整明文仅在生成结果中出现。"
        toolbar={
          <Badge variant="secondary" className="rounded-lg bg-slate-100 text-slate-700">
            {filteredItems.length} 项
          </Badge>
        }
      >
        <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4 xl:flex-row xl:items-center xl:justify-between">
          <div className="flex flex-1 flex-col gap-2 md:flex-row md:items-center">
            <div className="relative min-w-[240px] flex-1 md:max-w-sm">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-slate-400" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索预览码或使用者 ID"
                className="h-10 rounded-xl border-stone-200 bg-white pl-10"
              />
            </div>
            <Select value={typeFilter} onValueChange={(value) => setTypeFilter(value as RedeemCodeType | "all")}>
              <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white md:w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部类型</SelectItem>
                <SelectItem value="image_quota">GGB 兑换码</SelectItem>
                <SelectItem value="concurrency">图片并发</SelectItem>
                <SelectItem value="membership">会员兑换</SelectItem>
                <SelectItem value="invitation">邀请码</SelectItem>
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={(value) => setStatusFilter(value as RedeemStatusFilter)}>
              <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white md:w-[150px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="available">可用</SelectItem>
                <SelectItem value="used">已使用</SelectItem>
                <SelectItem value="disabled">禁用</SelectItem>
                <SelectItem value="expired">已过期</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center gap-3 px-6 py-16 text-sm text-slate-500">
            <LoaderCircle className="size-5 animate-spin" />
            正在加载兑换码
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="p-5">
            <EmptyState
              title="暂无兑换码"
              description="生成一批兑换码后，这里会展示预览、状态和使用记录。"
              icon={<Ticket className="size-7" />}
              action={
                <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" onClick={() => setIsGenerateOpen(true)}>
                  <Plus className="size-4" />
                  生成兑换码
                </Button>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table className="min-w-[1120px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>预览码</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead>数值</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>使用者 ID</TableHead>
                  <TableHead>使用时间</TableHead>
                  <TableHead>过期时间</TableHead>
                  <TableHead>创建时间</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredItems.map((item) => {
                  const status = getRedeemCodeStatus(item);

                  return (
                    <TableRow key={item.id}>
                      <TableCell>
                        <span className="font-mono text-sm font-semibold text-slate-900">{item.code_preview}</span>
                      </TableCell>
                      <TableCell>
                        <Badge variant={item.type === "invitation" ? "violet" : item.type === "membership" ? "warning" : item.type === "concurrency" ? "info" : "secondary"} className="rounded-md">
                          {getRedeemCodeTypeLabel(item.type)}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-medium text-slate-700">{getRedeemValueLabel(item)}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1.5">
                          <Badge variant={status.tone} className="rounded-md">
                            {status.label}
                          </Badge>
                          <Badge variant={item.enabled ? "success" : "secondary"} className="rounded-md">
                            {item.enabled ? "启用" : "禁用"}
                          </Badge>
                          {item.used ? (
                            <Badge variant="secondary" className="rounded-md">
                              已使用
                            </Badge>
                          ) : null}
                        </div>
                      </TableCell>
                      <TableCell className="max-w-[180px] truncate text-xs text-slate-500">
                        {item.used_by_user_id || "—"}
                      </TableCell>
                      <TableCell className="text-xs text-slate-500">{formatAdminDateTime(item.used_at)}</TableCell>
                      <TableCell className="text-xs text-slate-500">{formatAdminDateTime(item.expires_at)}</TableCell>
                      <TableCell className="text-xs text-slate-500">{formatAdminDateTime(item.created_at)}</TableCell>
                      <TableCell>
                        <div className="flex justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="rounded-xl text-slate-500 hover:text-slate-900"
                            disabled={pendingId === item.id}
                            onClick={() => openEditDialog(item)}
                          >
                            <Pencil className="size-4" />
                            <span className="sr-only">编辑</span>
                          </Button>
                          <Button
                            variant="ghost"
                            className="h-9 rounded-xl px-3 text-slate-500 hover:text-slate-900"
                            disabled={pendingId === item.id || item.used}
                            onClick={() => void handleToggle(item)}
                          >
                            {pendingId === item.id ? <LoaderCircle className="size-4 animate-spin" /> : null}
                            {item.enabled ? "禁用" : "启用"}
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="rounded-xl text-rose-500 hover:bg-rose-50 hover:text-rose-600"
                            disabled={pendingId === item.id}
                            onClick={() => setDeleteTarget(item)}
                          >
                            <Trash2 className="size-4" />
                            <span className="sr-only">删除</span>
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </DataPanel>

      <GenerateRedeemDialog
        open={isGenerateOpen}
        form={generateForm}
        plans={plans}
        isSubmitting={isGenerating}
        onOpenChange={(open) => setIsGenerateOpen(open)}
        onFormChange={setGenerateForm}
        onSubmit={() => void handleGenerate()}
      />

      <EditRedeemDialog
        open={Boolean(editingCode)}
        codePreview={editingCode?.code_preview || ""}
        form={editForm}
        isSubmitting={isUpdating}
        onOpenChange={(open) => {
          if (!open) setEditingCode(null);
        }}
        onFormChange={setEditForm}
        onSubmit={() => void handleUpdate()}
      />

      <Dialog open={isResultOpen} onOpenChange={setIsResultOpen}>
        <DialogContent showCloseButton={!isGenerating} className="w-[min(92vw,680px)]">
          <DialogHeader>
            <DialogTitle>明文兑换码</DialogTitle>
            <DialogDescription>这些明文兑换码仅在生成后展示一次。关闭前请复制保存。</DialogDescription>
          </DialogHeader>
          <textarea
            ref={generatedCodesTextareaRef}
            readOnly
            value={generatedCodes.join("\n")}
            className="h-72 w-full resize-none overflow-auto rounded-2xl border border-emerald-100 bg-emerald-50/70 p-4 font-mono text-xs leading-5 text-emerald-950 outline-none focus:border-emerald-300 focus:ring-2 focus:ring-emerald-100"
            aria-label="明文兑换码"
          />
          <DialogFooter>
            <Button variant="secondary" className="rounded-xl" onClick={() => setIsResultOpen(false)}>
              关闭
            </Button>
            <Button className="rounded-xl bg-emerald-600 text-white hover:bg-emerald-700" onClick={() => void copyGeneratedCodes()}>
              <Copy className="size-4" />
              复制全部
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(deleteTarget)} onOpenChange={(open) => (!open ? setDeleteTarget(null) : null)}>
        <DialogContent showCloseButton={!isDeleting}>
          <DialogHeader>
            <DialogTitle>删除兑换码</DialogTitle>
            <DialogDescription>确认删除 {deleteTarget?.code_preview}？此操作不会展示或恢复完整明文码。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" className="rounded-xl" disabled={isDeleting} onClick={() => setDeleteTarget(null)}>
              取消
            </Button>
            <Button className="rounded-xl bg-rose-600 text-white hover:bg-rose-700" disabled={isDeleting} onClick={() => void handleDelete()}>
              {isDeleting ? <LoaderCircle className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function GenerateRedeemDialog({
  open,
  form,
  isSubmitting,
  plans,
  onOpenChange,
  onFormChange,
  onSubmit,
}: {
  open: boolean;
  form: GenerateFormState;
  isSubmitting: boolean;
  plans: MembershipPlan[];
  onOpenChange: (open: boolean) => void;
  onFormChange: (form: GenerateFormState) => void;
  onSubmit: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={!isSubmitting}>
        <DialogHeader>
          <DialogTitle>生成兑换码</DialogTitle>
          <DialogDescription>可选过期时间会随生成请求提交到后端。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <Field>
            <FieldLabel>类型</FieldLabel>
            <Select
              value={form.type}
              onValueChange={(value) =>
                onFormChange({
                  ...form,
                  type: value as RedeemCodeType,
                  value: value === "invitation" || value === "membership" ? "0" : form.value === "0" ? "10" : form.value,
                })
              }
            >
              <SelectTrigger className="rounded-xl border-stone-200 bg-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="image_quota">GGB 兑换码</SelectItem>
                <SelectItem value="concurrency">图片并发</SelectItem>
                <SelectItem value="membership">会员兑换</SelectItem>
                <SelectItem value="invitation">邀请码</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          {form.type === "membership" ? (
            <Field>
              <FieldLabel>会员套餐</FieldLabel>
              <Select value={form.membership_plan_id} onValueChange={(value) => onFormChange({ ...form, membership_plan_id: value })}>
                <SelectTrigger className="rounded-xl border-stone-200 bg-white">
                  <SelectValue placeholder="请选择会员套餐" />
                </SelectTrigger>
                <SelectContent>
                  {plans.filter((plan) => plan.enabled).map((plan) => (
                    <SelectItem key={plan.id} value={plan.id}>
                      {plan.name} / {plan.period_image_quota} GGB 每 {plan.period_days} 天
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-slate-500">会员兑换码必须绑定一个已启用会员套餐。</p>
            </Field>
          ) : null}
          <div className="grid gap-4 md:grid-cols-2">
            <Field>
              <FieldLabel>{form.type === "image_quota" ? "GGB 数值" : "数值"}</FieldLabel>
              <Input
                value={form.value}
                type="number"
                min={form.type === "invitation" || form.type === "membership" ? 0 : 1}
                disabled={form.type === "invitation" || form.type === "membership"}
                className="rounded-xl"
                onChange={(event) => onFormChange({ ...form, value: event.target.value })}
              />
            </Field>
            <Field>
              <FieldLabel>生成数量</FieldLabel>
              <Input
                value={form.count}
                type="number"
                min={1}
                max={500}
                className="rounded-xl"
                onChange={(event) => onFormChange({ ...form, count: event.target.value })}
              />
            </Field>
          </div>
          <Field>
            <FieldLabel>过期时间（可选）</FieldLabel>
            <Input
              value={form.expires_at}
              type="datetime-local"
              className="rounded-xl"
              onChange={(event) => onFormChange({ ...form, expires_at: event.target.value })}
            />
          </Field>
        </div>
        <DialogFooter>
          <Button variant="secondary" className="rounded-xl" disabled={isSubmitting} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" disabled={isSubmitting} onClick={onSubmit}>
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : <Plus className="size-4" />}
            生成
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditRedeemDialog({
  open,
  codePreview,
  form,
  isSubmitting,
  onOpenChange,
  onFormChange,
  onSubmit,
}: {
  open: boolean;
  codePreview: string;
  form: EditFormState;
  isSubmitting: boolean;
  onOpenChange: (open: boolean) => void;
  onFormChange: (form: EditFormState) => void;
  onSubmit: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={!isSubmitting}>
        <DialogHeader>
          <DialogTitle>编辑兑换码</DialogTitle>
          <DialogDescription>更新启用状态和过期时间。预览码：{codePreview}</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <Field>
            <FieldLabel>状态</FieldLabel>
            <label className="flex h-11 items-center gap-3 rounded-xl border border-stone-200 bg-white px-4 text-sm text-stone-700">
              <Checkbox
                checked={form.enabled}
                onCheckedChange={(checked) => onFormChange({ ...form, enabled: Boolean(checked) })}
              />
              启用兑换码
            </label>
          </Field>
          <Field>
            <FieldLabel>过期时间（留空表示不过期）</FieldLabel>
            <Input
              value={form.expires_at}
              type="datetime-local"
              className="rounded-xl"
              onChange={(event) => onFormChange({ ...form, expires_at: event.target.value })}
            />
          </Field>
        </div>
        <DialogFooter>
          <Button variant="secondary" className="rounded-xl" disabled={isSubmitting} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" disabled={isSubmitting} onClick={onSubmit}>
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            保存修改
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
