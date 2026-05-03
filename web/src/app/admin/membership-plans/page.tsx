"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Crown, LoaderCircle, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { DataPanel } from "@/components/common/data-panel";
import { EmptyState } from "@/components/common/empty-state";
import { PageHeader } from "@/components/common/page-header";
import { StatCard } from "@/components/common/stat-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { createMembershipPlan, deleteMembershipPlan, fetchAdminMembershipPlans, updateMembershipPlan, type MembershipPlan } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

type PlanFormState = {
  name: string;
  description: string;
  duration_days: string;
  period_days: string;
  period_image_quota: string;
  enabled: boolean;
  sort_order: string;
};

const defaultForm: PlanFormState = {
  name: "",
  description: "",
  duration_days: "30",
  period_days: "30",
  period_image_quota: "100",
  enabled: true,
  sort_order: "0",
};

function toInteger(value: string, fallback: number, min = 0) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(min, Math.trunc(numeric));
}

function buildForm(plan: MembershipPlan): PlanFormState {
  return {
    name: plan.name,
    description: plan.description || "",
    duration_days: String(plan.duration_days),
    period_days: String(plan.period_days),
    period_image_quota: String(plan.period_image_quota),
    enabled: plan.enabled,
    sort_order: String(plan.sort_order),
  };
}

function buildPayload(form: PlanFormState) {
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    duration_days: toInteger(form.duration_days, 30, 1),
    period_days: toInteger(form.period_days, 30, 1),
    period_image_quota: toInteger(form.period_image_quota, 0, 0),
    enabled: form.enabled,
    sort_order: toInteger(form.sort_order, 0, 0),
  };
}

export default function AdminMembershipPlansPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  const didLoadRef = useRef(false);
  const [items, setItems] = useState<MembershipPlan[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<PlanFormState>(defaultForm);
  const [editingPlan, setEditingPlan] = useState<MembershipPlan | null>(null);
  const [editForm, setEditForm] = useState<PlanFormState>(defaultForm);
  const [deleteTarget, setDeleteTarget] = useState<MembershipPlan | null>(null);
  const [pendingId, setPendingId] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const canLoadAdminData = !isCheckingAuth && session?.role === "admin";

  const load = async (silent = false) => {
    if (!silent) setIsLoading(true);
    try {
      const data = await fetchAdminMembershipPlans();
      setItems(data.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载会员套餐失败");
    } finally {
      if (!silent) setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!canLoadAdminData || didLoadRef.current) return;
    didLoadRef.current = true;
    void load();
  }, [canLoadAdminData]);

  const summary = useMemo(() => {
    const enabled = items.filter((item) => item.enabled).length;
    const quota = items.reduce((sum, item) => sum + Math.max(0, item.period_image_quota), 0);
    return { total: items.length, enabled, quota };
  }, [items]);

  const handleCreate = async () => {
    const payload = buildPayload(createForm);
    if (!payload.name) {
      toast.error("请输入套餐名称");
      return;
    }
    if (payload.period_days > payload.duration_days) {
      toast.error("周期天数不能超过有效天数");
      return;
    }
    setIsSubmitting(true);
    try {
      const data = await createMembershipPlan(payload);
      setItems(data.items);
      setCreateForm(defaultForm);
      setIsCreateOpen(false);
      toast.success("会员套餐已创建");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建会员套餐失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleUpdate = async () => {
    if (!editingPlan) return;
    const payload = buildPayload(editForm);
    if (!payload.name) {
      toast.error("请输入套餐名称");
      return;
    }
    if (payload.period_days > payload.duration_days) {
      toast.error("周期天数不能超过有效天数");
      return;
    }
    setIsSubmitting(true);
    setPendingId(editingPlan.id);
    try {
      const data = await updateMembershipPlan(editingPlan.id, payload);
      setItems(data.items);
      setEditingPlan(null);
      toast.success("会员套餐已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新会员套餐失败");
    } finally {
      setIsSubmitting(false);
      setPendingId("");
    }
  };

  const handleToggle = async (plan: MembershipPlan) => {
    setPendingId(plan.id);
    try {
      const data = await updateMembershipPlan(plan.id, { enabled: !plan.enabled });
      setItems(data.items);
      toast.success(plan.enabled ? "会员套餐已禁用" : "会员套餐已启用");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新会员套餐失败");
    } finally {
      setPendingId("");
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsSubmitting(true);
    setPendingId(deleteTarget.id);
    try {
      const data = await deleteMembershipPlan(deleteTarget.id);
      setItems(data.items);
      setDeleteTarget(null);
      toast.success("会员套餐已删除或禁用");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除会员套餐失败");
    } finally {
      setIsSubmitting(false);
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
        eyebrow="Membership Plans"
        title="会员套餐管理"
        description="创建、更新、启用、排序和删除会员套餐。被兑换码或会员记录引用的套餐删除时会由后端禁用。"
        actions={
          <>
            <Button variant="outline" className="h-10 rounded-xl border-stone-200 bg-white/85" disabled={isLoading} onClick={() => void load()}>
              <RefreshCw className={cn("size-4", isLoading ? "animate-spin" : "")} />
              刷新
            </Button>
            <Button className="h-10 rounded-xl bg-slate-950 text-white hover:bg-slate-800" onClick={() => setIsCreateOpen(true)}>
              <Plus className="size-4" />
              创建套餐
            </Button>
          </>
        }
      />

      <div className="grid gap-3 md:grid-cols-3">
        <StatCard label="套餐总数" value={summary.total} icon={<Crown className="size-5" />} tone="slate" />
        <StatCard label="已启用" value={summary.enabled} icon={<Crown className="size-5" />} tone="emerald" />
        <StatCard label="周期 GGB 合计" value={`${summary.quota} GGB`} icon={<Plus className="size-5" />} tone="blue" />
      </div>

      <DataPanel title="会员套餐列表" description="排序值越小越靠前，用户会员中心和兑换码生成表单会按后端返回顺序展示。">
        {isLoading ? (
          <div className="flex items-center justify-center gap-3 px-6 py-16 text-sm text-slate-500">
            <LoaderCircle className="size-5 animate-spin" />
            正在加载会员套餐
          </div>
        ) : items.length === 0 ? (
          <div className="p-5">
            <EmptyState title="暂无会员套餐" description="创建套餐后即可用于生成会员兑换码。" icon={<Crown className="size-7" />} />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table className="min-w-[980px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>套餐</TableHead>
                  <TableHead>周期 GGB</TableHead>
                  <TableHead>有效天数</TableHead>
                  <TableHead>周期天数</TableHead>
                  <TableHead>排序</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell>
                      <div className="font-semibold text-slate-900">{item.name}</div>
                      <div className="mt-1 max-w-[360px] truncate text-xs text-slate-500">{item.description || "—"}</div>
                    </TableCell>
                    <TableCell className="font-semibold text-slate-700">{item.period_image_quota} GGB</TableCell>
                    <TableCell>{item.duration_days} 天</TableCell>
                    <TableCell>{item.period_days} 天</TableCell>
                    <TableCell>{item.sort_order}</TableCell>
                    <TableCell>
                      <Badge variant={item.enabled ? "success" : "secondary"} className="rounded-md">
                        {item.enabled ? "启用" : "禁用"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" className="rounded-xl text-slate-500 hover:text-slate-900" disabled={pendingId === item.id} onClick={() => { setEditingPlan(item); setEditForm(buildForm(item)); }}>
                          <Pencil className="size-4" />
                          <span className="sr-only">编辑</span>
                        </Button>
                        <Button variant="ghost" className="h-9 rounded-xl px-3 text-slate-500 hover:text-slate-900" disabled={pendingId === item.id} onClick={() => void handleToggle(item)}>
                          {pendingId === item.id ? <LoaderCircle className="size-4 animate-spin" /> : null}
                          {item.enabled ? "禁用" : "启用"}
                        </Button>
                        <Button variant="ghost" size="icon" className="rounded-xl text-rose-500 hover:bg-rose-50 hover:text-rose-600" disabled={pendingId === item.id} onClick={() => setDeleteTarget(item)}>
                          <Trash2 className="size-4" />
                          <span className="sr-only">删除</span>
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </DataPanel>

      <PlanDialog mode="create" open={isCreateOpen} form={createForm} isSubmitting={isSubmitting} onOpenChange={setIsCreateOpen} onFormChange={setCreateForm} onSubmit={() => void handleCreate()} />
      <PlanDialog mode="edit" open={Boolean(editingPlan)} form={editForm} isSubmitting={isSubmitting} onOpenChange={(open) => { if (!open) setEditingPlan(null); }} onFormChange={setEditForm} onSubmit={() => void handleUpdate()} />

      <Dialog open={Boolean(deleteTarget)} onOpenChange={(open) => (!open ? setDeleteTarget(null) : null)}>
        <DialogContent showCloseButton={!isSubmitting}>
          <DialogHeader>
            <DialogTitle>删除会员套餐</DialogTitle>
            <DialogDescription>确认删除 {deleteTarget?.name}？如果已被引用，后端会禁用该套餐以保留历史关联。</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" className="rounded-xl" disabled={isSubmitting} onClick={() => setDeleteTarget(null)}>取消</Button>
            <Button className="rounded-xl bg-rose-600 text-white hover:bg-rose-700" disabled={isSubmitting} onClick={() => void handleDelete()}>
              {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : <Trash2 className="size-4" />}
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function PlanDialog({
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
  form: PlanFormState;
  isSubmitting: boolean;
  onOpenChange: (open: boolean) => void;
  onFormChange: (form: PlanFormState) => void;
  onSubmit: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={!isSubmitting} className="max-h-[92vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{mode === "create" ? "创建会员套餐" : "编辑会员套餐"}</DialogTitle>
          <DialogDescription>设置套餐名称、说明、有效期、周期 GGB、启用状态和排序。</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <Field>
            <FieldLabel>套餐名称</FieldLabel>
            <Input value={form.name} className="rounded-xl" onChange={(event) => onFormChange({ ...form, name: event.target.value })} />
          </Field>
          <Field>
            <FieldLabel>套餐说明</FieldLabel>
            <Textarea value={form.description} className="min-h-24 rounded-xl" onChange={(event) => onFormChange({ ...form, description: event.target.value })} />
          </Field>
          <div className="grid gap-4 md:grid-cols-2">
            <Field>
              <FieldLabel>有效天数</FieldLabel>
              <Input value={form.duration_days} type="number" min={1} className="rounded-xl" onChange={(event) => onFormChange({ ...form, duration_days: event.target.value })} />
            </Field>
            <Field>
              <FieldLabel>周期天数</FieldLabel>
              <Input value={form.period_days} type="number" min={1} className="rounded-xl" onChange={(event) => onFormChange({ ...form, period_days: event.target.value })} />
            </Field>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Field>
              <FieldLabel>周期 GGB</FieldLabel>
              <Input value={form.period_image_quota} type="number" min={0} className="rounded-xl" onChange={(event) => onFormChange({ ...form, period_image_quota: event.target.value })} />
            </Field>
            <Field>
              <FieldLabel>排序</FieldLabel>
              <Input value={form.sort_order} type="number" min={0} className="rounded-xl" onChange={(event) => onFormChange({ ...form, sort_order: event.target.value })} />
            </Field>
          </div>
          <Field>
            <FieldLabel>状态</FieldLabel>
            <label className="flex h-11 items-center gap-3 rounded-xl border border-stone-200 bg-white px-4 text-sm text-stone-700">
              <Checkbox checked={form.enabled} onCheckedChange={(checked) => onFormChange({ ...form, enabled: Boolean(checked) })} />
              启用会员套餐
            </label>
          </Field>
        </div>
        <DialogFooter>
          <Button variant="secondary" className="rounded-xl" disabled={isSubmitting} onClick={() => onOpenChange(false)}>取消</Button>
          <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" disabled={isSubmitting} onClick={onSubmit}>
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            {mode === "create" ? "创建套餐" : "保存修改"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
