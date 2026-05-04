"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Gift, LoaderCircle, Pencil, Percent, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
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
import { createPromoCode, deletePromoCode, fetchPromoCodes, updatePromoCode, type PromoCode } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

import {
  datetimeLocalToApiValue,
  formatAdminDateTime,
  formatUsageLimit,
  getPromoCodeStatus,
  toDatetimeLocalValue,
  type PromoCodeStatusKey,
} from "./components/promo-code-helpers";

type PromoStatusFilter = "all" | PromoCodeStatusKey;

type PromoFormState = {
  code: string;
  image_quota: string;
  max_uses: string;
  enabled: boolean;
  expires_at: string;
};

const defaultCreateForm: PromoFormState = {
  code: "",
  image_quota: "5",
  max_uses: "100",
  enabled: true,
  expires_at: "",
};

function buildEditForm(item: PromoCode): PromoFormState {
  return {
    code: item.code_preview,
    image_quota: String(item.image_quota),
    max_uses: String(item.max_uses),
    enabled: item.enabled,
    expires_at: toDatetimeLocalValue(item.expires_at),
  };
}

function nonNegativeInteger(value: string, fallback = 0) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(0, Math.trunc(numeric));
}

function positiveInteger(value: string, fallback = 1) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(1, Math.trunc(numeric));
}

export default function AdminPromoCodesPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  const didLoadRef = useRef(false);
  const [items, setItems] = useState<PromoCode[]>([]);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<PromoStatusFilter>("all");
  const [isLoading, setIsLoading] = useState(true);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<PromoFormState>(defaultCreateForm);
  const [isCreating, setIsCreating] = useState(false);
  const [editingCode, setEditingCode] = useState<PromoCode | null>(null);
  const [editForm, setEditForm] = useState<PromoFormState>(defaultCreateForm);
  const [isUpdating, setIsUpdating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<PromoCode | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [pendingId, setPendingId] = useState("");
  const canLoadAdminData = !isCheckingAuth && session?.role === "admin";

  const load = async (silent = false) => {
    if (!silent) setIsLoading(true);
    try {
      const data = await fetchPromoCodes();
      setItems(data.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载优惠码失败");
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
      const status = getPromoCodeStatus(item).key;
      const statusMatched = statusFilter === "all" || status === statusFilter;
      const queryMatched = !normalizedQuery || item.code_preview.toLowerCase().includes(normalizedQuery);
      return statusMatched && queryMatched;
    });
  }, [items, query, statusFilter]);

  const summary = useMemo(() => {
    return items.reduce(
      (acc, item) => {
        acc.total += 1;
        acc.uses += item.used_count;
        acc.quota += Math.max(0, item.image_quota);
        if (getPromoCodeStatus(item).key === "active") acc.active += 1;
        return acc;
      },
      { total: 0, active: 0, uses: 0, quota: 0 },
    );
  }, [items]);

  const resetCreateDialog = () => {
    setCreateForm(defaultCreateForm);
    setIsCreateOpen(false);
  };

  const handleCreate = async () => {
    if (!createForm.code.trim()) {
      toast.error("请输入优惠码");
      return;
    }

    setIsCreating(true);
    try {
      const expiresAt = datetimeLocalToApiValue(createForm.expires_at);
      const data = await createPromoCode({
        code: createForm.code.trim(),
        image_quota: nonNegativeInteger(createForm.image_quota),
        max_uses: positiveInteger(createForm.max_uses),
        enabled: createForm.enabled,
        ...(expiresAt ? { expires_at: expiresAt } : {}),
      });
      setItems(data.items);
      resetCreateDialog();
      toast.success("优惠码已创建；列表仅保留预览码");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建优惠码失败");
    } finally {
      setIsCreating(false);
    }
  };

  const openEditDialog = (item: PromoCode) => {
    setEditingCode(item);
    setEditForm(buildEditForm(item));
  };

  const handleUpdate = async () => {
    if (!editingCode) return;
    setIsUpdating(true);
    setPendingId(editingCode.id);
    try {
      const expiresAt = datetimeLocalToApiValue(editForm.expires_at);
      const data = await updatePromoCode(editingCode.id, {
        image_quota: nonNegativeInteger(editForm.image_quota),
        max_uses: positiveInteger(editForm.max_uses),
        enabled: editForm.enabled,
        expires_at: expiresAt ?? "",
      });
      setItems(data.items);
      setEditingCode(null);
      toast.success("优惠码已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新优惠码失败");
    } finally {
      setIsUpdating(false);
      setPendingId("");
    }
  };

  const handleToggle = async (item: PromoCode) => {
    setPendingId(item.id);
    try {
      const data = await updatePromoCode(item.id, { enabled: !item.enabled });
      setItems(data.items);
      toast.success(item.enabled ? "优惠码已禁用" : "优惠码已启用");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新优惠码失败");
    } finally {
      setPendingId("");
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    setPendingId(deleteTarget.id);
    try {
      const data = await deletePromoCode(deleteTarget.id);
      setItems(data.items);
      setDeleteTarget(null);
      toast.success("优惠码已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除优惠码失败");
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
        eyebrow="Promo Codes"
        title="优惠码管理"
        description="后端列表只返回 code_preview，创建后不会在界面中伪造或恢复完整码。"
        actions={
          <>
            <Button variant="outline" className="h-10 rounded-xl border-stone-200 bg-white/85" disabled={isLoading} onClick={() => void load()}>
              <RefreshCw className={cn("size-4", isLoading ? "animate-spin" : "")} />
              刷新
            </Button>
            <Button className="h-10 rounded-xl bg-slate-950 text-white hover:bg-slate-800" onClick={() => setIsCreateOpen(true)}>
              <Plus className="size-4" />
              创建优惠码
            </Button>
          </>
        }
      />

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="优惠码总数" value={summary.total} icon={<Percent className="size-5" />} tone="slate" />
        <StatCard label="可用" value={summary.active} icon={<Gift className="size-5" />} tone="emerald" />
        <StatCard label="累计使用" value={summary.uses} icon={<Percent className="size-5" />} tone="blue" />
        <StatCard label="总赠送 GGB" value={`${summary.quota} GGB`} icon={<Plus className="size-5" />} tone="teal" />
      </div>

      <DataPanel
        title="优惠码列表"
        description="只显示预览码、使用次数、启用状态和过期状态。"
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
                placeholder="搜索预览码"
                className="h-10 rounded-xl border-stone-200 bg-white pl-10 font-mono"
              />
            </div>
            <Select value={statusFilter} onValueChange={(value) => setStatusFilter(value as PromoStatusFilter)}>
              <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white md:w-[160px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="active">可用</SelectItem>
                <SelectItem value="disabled">禁用</SelectItem>
                <SelectItem value="expired">已过期</SelectItem>
                <SelectItem value="exhausted">已用尽</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center gap-3 px-6 py-16 text-sm text-slate-500">
            <LoaderCircle className="size-5 animate-spin" />
            正在加载优惠码
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="p-5">
            <EmptyState
              title="暂无优惠码"
              icon={<Percent className="size-7" />}
              action={
                <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" onClick={() => setIsCreateOpen(true)}>
                  <Plus className="size-4" />
                  创建优惠码
                </Button>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table className="min-w-[980px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>预览码</TableHead>
                  <TableHead>赠送 GGB</TableHead>
                  <TableHead>使用次数</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>创建时间</TableHead>
                  <TableHead>过期时间</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredItems.map((item) => {
                  const status = getPromoCodeStatus(item);

                  return (
                    <TableRow key={item.id}>
                      <TableCell>
                        <span className="font-mono text-sm font-semibold text-slate-900">{item.code_preview}</span>
                      </TableCell>
                      <TableCell className="font-medium text-slate-700">+{item.image_quota} GGB</TableCell>
                      <TableCell className="text-slate-600">{formatUsageLimit(item)}</TableCell>
                      <TableCell>
                        <div className="flex flex-wrap gap-1.5">
                          <Badge variant={status.tone} className="rounded-md">
                            {status.label}
                          </Badge>
                          <Badge variant={item.enabled ? "success" : "secondary"} className="rounded-md">
                            {item.enabled ? "启用" : "禁用"}
                          </Badge>
                        </div>
                      </TableCell>
                      <TableCell className="text-xs text-slate-500">{formatAdminDateTime(item.created_at)}</TableCell>
                      <TableCell className="text-xs text-slate-500">{formatAdminDateTime(item.expires_at)}</TableCell>
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
                            disabled={pendingId === item.id}
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

      <PromoCodeDialog
        mode="create"
        open={isCreateOpen}
        form={createForm}
        isSubmitting={isCreating}
        onOpenChange={(open) => (open ? setIsCreateOpen(true) : resetCreateDialog())}
        onFormChange={setCreateForm}
        onSubmit={() => void handleCreate()}
      />

      <PromoCodeDialog
        mode="edit"
        open={Boolean(editingCode)}
        form={editForm}
        isSubmitting={isUpdating}
        onOpenChange={(open) => {
          if (!open) setEditingCode(null);
        }}
        onFormChange={setEditForm}
        onSubmit={() => void handleUpdate()}
      />

      <Dialog open={Boolean(deleteTarget)} onOpenChange={(open) => (!open ? setDeleteTarget(null) : null)}>
        <DialogContent showCloseButton={!isDeleting}>
          <DialogHeader>
            <DialogTitle>删除优惠码</DialogTitle>
            <DialogDescription>确认删除 {deleteTarget?.code_preview}？列表中没有完整码，删除后无法恢复。</DialogDescription>
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

function PromoCodeDialog({
  mode,
  open,
  form,
  isSubmitting,
  onOpenChange,
  onFormChange,
  onSubmit,
}: {
  mode: "create" | "edit";
  open: boolean;
  form: PromoFormState;
  isSubmitting: boolean;
  onOpenChange: (open: boolean) => void;
  onFormChange: (form: PromoFormState) => void;
  onSubmit: () => void;
}) {
  const isEdit = mode === "edit";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={!isSubmitting}>
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑优惠码" : "创建优惠码"}</DialogTitle>
          <DialogDescription>
            {isEdit
              ? "完整优惠码不会从后端列表返回；这里只保留并展示 code_preview。"
              : "创建时需要输入完整优惠码。创建后列表只保留预览码。"}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <Field>
            <FieldLabel>{isEdit ? "预览码" : "优惠码"}</FieldLabel>
            <Input
              value={form.code}
              disabled={isEdit}
              className="rounded-xl font-mono uppercase"
              placeholder="WELCOME"
              onChange={(event) => onFormChange({ ...form, code: event.target.value })}
            />
            {isEdit ? (
              <p className="text-xs text-slate-500">出于安全设计，编辑时不回填完整码，也不会把预览码当作完整码提交。</p>
            ) : null}
          </Field>
          <div className="grid gap-4 md:grid-cols-2">
            <Field>
              <FieldLabel>赠送 GGB</FieldLabel>
              <Input
                value={form.image_quota}
                type="number"
                min={0}
                className="rounded-xl"
                onChange={(event) => onFormChange({ ...form, image_quota: event.target.value })}
              />
            </Field>
            <Field>
              <FieldLabel>最大使用次数</FieldLabel>
              <Input
                value={form.max_uses}
                type="number"
                min={1}
                className="rounded-xl"
                onChange={(event) => onFormChange({ ...form, max_uses: event.target.value })}
              />
            </Field>
          </div>
          <Field>
            <FieldLabel>状态</FieldLabel>
            <label className="flex h-11 items-center gap-3 rounded-xl border border-stone-200 bg-white px-4 text-sm text-stone-700">
              <Checkbox
                checked={form.enabled}
                onCheckedChange={(checked) => onFormChange({ ...form, enabled: Boolean(checked) })}
              />
              启用优惠码
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
            {isEdit ? "保存修改" : "创建优惠码"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
