"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, Pencil, Plus, RefreshCw, Search, ShieldCheck, Trash2, UserCheck, Users } from "lucide-react";
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
import { createManagedUser, deleteManagedUser, fetchManagedUsers, updateManagedUser, type AuthRole, type ManagedUser } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

import {
  canDisableOrDeleteUser,
  coerceNonNegativeInteger,
  coercePositiveInteger,
  formatUserDateTime,
  getUserRoleLabel,
  getUserStatus,
} from "./components/user-management-helpers";

type StatusFilter = "all" | "enabled" | "disabled";

type UserFormState = {
  email: string;
  password: string;
  role: AuthRole;
  enabled: boolean;
  image_quota: string;
  image_concurrency: string;
};

const defaultCreateForm: UserFormState = {
  email: "",
  password: "",
  role: "user",
  enabled: true,
  image_quota: "0",
  image_concurrency: "1",
};

function buildEditForm(item: ManagedUser): UserFormState {
  return {
    email: item.email,
    password: "",
    role: item.role,
    enabled: item.enabled,
    image_quota: String(item.image_quota),
    image_concurrency: String(item.image_concurrency),
  };
}

export default function AdminUsersPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  const didLoadRef = useRef(false);
  const [items, setItems] = useState<ManagedUser[]>([]);
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<AuthRole | "all">("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [isLoading, setIsLoading] = useState(true);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [editingUser, setEditingUser] = useState<ManagedUser | null>(null);
  const [editForm, setEditForm] = useState<UserFormState>(defaultCreateForm);
  const [isUpdating, setIsUpdating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ManagedUser | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [pendingId, setPendingId] = useState("");
  const [createForm, setCreateForm] = useState<UserFormState>(defaultCreateForm);
  const canLoadAdminData = !isCheckingAuth && session?.role === "admin";

  const load = async (nextQuery = query, silent = false) => {
    if (!silent) setIsLoading(true);
    try {
      const data = await fetchManagedUsers(nextQuery);
      setItems(data.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载用户失败");
    } finally {
      if (!silent) setIsLoading(false);
    }
  };

  useEffect(() => {
    if (!canLoadAdminData || didLoadRef.current) return;
    didLoadRef.current = true;
    void load("");
  }, [canLoadAdminData]);

  const filteredItems = useMemo(() => {
    return items.filter((item) => {
      const roleMatched = roleFilter === "all" || item.role === roleFilter;
      const statusMatched =
        statusFilter === "all" || (statusFilter === "enabled" ? item.enabled : !item.enabled);
      return roleMatched && statusMatched;
    });
  }, [items, roleFilter, statusFilter]);

  const summary = useMemo(() => {
    const enabled = items.filter((item) => item.enabled).length;
    const admins = items.filter((item) => item.role === "admin").length;
    const quota = items.reduce((sum, item) => sum + Math.max(0, item.total_image_quota ?? item.image_quota), 0);
    const memberQuota = items.reduce((sum, item) => sum + Math.max(0, item.member_image_quota ?? 0), 0);
    return { total: items.length, enabled, admins, quota, memberQuota };
  }, [items]);

  const resetCreateDialog = () => {
    setCreateForm(defaultCreateForm);
    setIsCreateOpen(false);
  };

  const handleCreate = async () => {
    if (!createForm.email.trim() || !createForm.password) {
      toast.error("请输入邮箱和密码");
      return;
    }

    setIsCreating(true);
    try {
      const data = await createManagedUser({
        email: createForm.email.trim().toLowerCase(),
        password: createForm.password,
        role: createForm.role,
        enabled: createForm.enabled,
        image_quota: coerceNonNegativeInteger(createForm.image_quota),
        image_concurrency:
          createForm.role === "admin" ? 0 : coercePositiveInteger(createForm.image_concurrency),
      });
      setItems(data.items);
      resetCreateDialog();
      toast.success("用户已创建");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建用户失败");
    } finally {
      setIsCreating(false);
    }
  };

  const openEditDialog = (item: ManagedUser) => {
    setEditingUser(item);
    setEditForm(buildEditForm(item));
  };

  const handleUpdate = async () => {
    if (!editingUser || !session) return;
    if (!editForm.email.trim()) {
      toast.error("请输入邮箱");
      return;
    }

    const isSelf = editingUser.id === session.subjectId;
    setIsUpdating(true);
    setPendingId(editingUser.id);
    try {
      const updates = {
        email: editForm.email.trim().toLowerCase(),
        role: editForm.role,
        enabled: isSelf ? true : editForm.enabled,
        image_quota: coerceNonNegativeInteger(editForm.image_quota),
        image_concurrency: editForm.role === "admin" ? 0 : coercePositiveInteger(editForm.image_concurrency),
        ...(editForm.password.trim() ? { password: editForm.password } : {}),
      };
      const data = await updateManagedUser(editingUser.id, updates);
      setItems(data.items);
      setEditingUser(null);
      toast.success("用户已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新用户失败");
    } finally {
      setIsUpdating(false);
      setPendingId("");
    }
  };

  const handleToggle = async (item: ManagedUser) => {
    if (!session || !canDisableOrDeleteUser(item.id, session.subjectId)) return;
    setPendingId(item.id);
    try {
      const data = await updateManagedUser(item.id, { enabled: !item.enabled });
      setItems(data.items);
      toast.success(item.enabled ? "用户已禁用" : "用户已启用");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新用户失败");
    } finally {
      setPendingId("");
    }
  };

  const openDeleteDialog = (item: ManagedUser) => {
    if (!session || !canDisableOrDeleteUser(item.id, session.subjectId)) return;
    setDeleteTarget(item);
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    setPendingId(deleteTarget.id);
    try {
      const data = await deleteManagedUser(deleteTarget.id);
      setItems(data.items);
      setDeleteTarget(null);
      toast.success("用户已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除用户失败");
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
        eyebrow="Users"
        title="用户管理"
        description="管理后台用户身份、登录状态、GGB 余额和图片并发限制。当前会话用户不能在界面中被禁用或删除。"
        actions={
          <>
            <Button
              variant="outline"
              className="h-10 rounded-xl border-stone-200 bg-white/85"
              disabled={isLoading}
              onClick={() => void load(query)}
            >
              <RefreshCw className={cn("size-4", isLoading ? "animate-spin" : "")} />
              刷新
            </Button>
            <Button className="h-10 rounded-xl bg-slate-950 text-white hover:bg-slate-800" onClick={() => setIsCreateOpen(true)}>
              <Plus className="size-4" />
              创建用户
            </Button>
          </>
        }
      />

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <StatCard label="用户总数" value={summary.total} icon={<Users className="size-5" />} tone="slate" />
        <StatCard label="已启用" value={summary.enabled} icon={<UserCheck className="size-5" />} tone="emerald" />
        <StatCard label="管理员" value={summary.admins} icon={<ShieldCheck className="size-5" />} tone="teal" />
        <StatCard label="GGB 余额" value={`${summary.quota} GGB`} icon={<Plus className="size-5" />} tone="blue" />
        <StatCard label="会员 GGB" value={`${summary.memberQuota} GGB`} icon={<Plus className="size-5" />} tone="amber" />
      </div>

      <DataPanel
        title="用户列表"
        description="按邮箱搜索会请求后端，角色和状态筛选在当前结果内应用。"
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
                onKeyDown={(event) => {
                  if (event.key === "Enter") void load(query);
                }}
                placeholder="搜索邮箱"
                className="h-10 rounded-xl border-stone-200 bg-white pl-10"
              />
            </div>
            <Select value={roleFilter} onValueChange={(value) => setRoleFilter(value as AuthRole | "all")}>
              <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white md:w-[150px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部角色</SelectItem>
                <SelectItem value="admin">管理员</SelectItem>
                <SelectItem value="user">普通用户</SelectItem>
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={(value) => setStatusFilter(value as StatusFilter)}>
              <SelectTrigger className="h-10 rounded-xl border-stone-200 bg-white md:w-[150px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="enabled">启用</SelectItem>
                <SelectItem value="disabled">禁用</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <Button variant="outline" className="h-10 rounded-xl border-stone-200 bg-white" onClick={() => void load(query)}>
            <Search className="size-4" />
            搜索
          </Button>
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center gap-3 px-6 py-16 text-sm text-slate-500">
            <LoaderCircle className="size-5 animate-spin" />
            正在加载用户
          </div>
        ) : filteredItems.length === 0 ? (
          <div className="p-5">
            <EmptyState
              title="暂无匹配用户"
              description="调整搜索条件，或创建新的后台用户。"
              icon={<Users className="size-7" />}
              action={
                <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" onClick={() => setIsCreateOpen(true)}>
                  <Plus className="size-4" />
                  创建用户
                </Button>
              }
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <Table className="min-w-[1180px]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>用户</TableHead>
                  <TableHead>角色</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>GGB 余额</TableHead>
                  <TableHead>会员 GGB</TableHead>
                  <TableHead>会员状态</TableHead>
                  <TableHead>图片并发</TableHead>
                  <TableHead>活动请求</TableHead>
                  <TableHead>创建时间</TableHead>
                  <TableHead>最近登录</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredItems.map((item) => {
                  const isSelf = item.id === session.subjectId;
                  const canMutateStatus = canDisableOrDeleteUser(item.id, session.subjectId);
                  const status = getUserStatus(item);

                  return (
                    <TableRow key={item.id}>
                      <TableCell>
                        <div className="flex items-center gap-3">
                          <div className="grid size-9 shrink-0 place-items-center rounded-2xl bg-slate-100 text-sm font-black text-slate-600">
                            {item.email.slice(0, 1).toUpperCase()}
                          </div>
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="truncate font-semibold text-slate-900">{item.email}</span>
                              {isSelf ? (
                                <Badge variant="info" className="rounded-md">
                                  当前会话
                                </Badge>
                              ) : null}
                            </div>
                            <div className="mt-1 truncate text-xs text-slate-400">{item.id}</div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant={item.role === "admin" ? "violet" : "secondary"} className="rounded-md">
                          {getUserRoleLabel(item.role)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant={status.tone} className="rounded-md">
                          {status.label}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-medium text-slate-700">{item.total_image_quota ?? item.image_quota} GGB</TableCell>
                      <TableCell className="font-medium text-slate-700">{item.member_image_quota ?? 0} GGB</TableCell>
                      <TableCell>
                        <Badge variant={item.membership_status === "active" ? "warning" : "secondary"} className="rounded-md">
                          {item.membership_status === "active" ? item.membership_plan_name || "会员" : item.membership_status === "expired" ? "已过期" : "未开通"}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-medium text-slate-700">{item.image_concurrency}</TableCell>
                      <TableCell className="text-slate-500">{item.active_image_requests}</TableCell>
                      <TableCell className="text-xs text-slate-500">{formatUserDateTime(item.created_at)}</TableCell>
                      <TableCell className="text-xs text-slate-500">{formatUserDateTime(item.last_login_at)}</TableCell>
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
                            disabled={pendingId === item.id || !canMutateStatus}
                            onClick={() => void handleToggle(item)}
                          >
                            {pendingId === item.id ? <LoaderCircle className="size-4 animate-spin" /> : null}
                            {item.enabled ? "禁用" : "启用"}
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="rounded-xl text-rose-500 hover:bg-rose-50 hover:text-rose-600"
                            disabled={pendingId === item.id || !canMutateStatus}
                            onClick={() => openDeleteDialog(item)}
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

      <UserFormDialog
        mode="create"
        open={isCreateOpen}
        form={createForm}
        isSelf={false}
        isSubmitting={isCreating}
        onOpenChange={(open) => (open ? setIsCreateOpen(true) : resetCreateDialog())}
        onFormChange={setCreateForm}
        onSubmit={() => void handleCreate()}
      />

      <UserFormDialog
        mode="edit"
        open={Boolean(editingUser)}
        form={editForm}
        isSelf={editingUser?.id === session.subjectId}
        isSubmitting={isUpdating}
        onOpenChange={(open) => {
          if (!open) setEditingUser(null);
        }}
        onFormChange={setEditForm}
        onSubmit={() => void handleUpdate()}
      />

      <Dialog open={Boolean(deleteTarget)} onOpenChange={(open) => (!open ? setDeleteTarget(null) : null)}>
        <DialogContent showCloseButton={!isDeleting}>
          <DialogHeader>
            <DialogTitle>删除用户</DialogTitle>
            <DialogDescription>
              删除 {deleteTarget?.email}。如果该用户已有使用历史，后端会保留记录并禁用账号。
            </DialogDescription>
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

function UserFormDialog({
  mode,
  open,
  form,
  isSelf,
  isSubmitting,
  onOpenChange,
  onFormChange,
  onSubmit,
}: {
  mode: "create" | "edit";
  open: boolean;
  form: UserFormState;
  isSelf: boolean;
  isSubmitting: boolean;
  onOpenChange: (open: boolean) => void;
  onFormChange: (form: UserFormState) => void;
  onSubmit: () => void;
}) {
  const isEdit = mode === "edit";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={!isSubmitting} className="max-h-[92vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑用户" : "创建用户"}</DialogTitle>
          <DialogDescription>
            {isEdit ? "更新邮箱、密码、角色、启用状态、GGB 余额和图片并发限制。" : "创建可登录的后台用户，并设置初始 GGB 余额和图片并发限制。"}
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4">
          <Field>
            <FieldLabel>邮箱</FieldLabel>
            <Input
              value={form.email}
              type="email"
              className="rounded-xl"
              placeholder="name@example.com"
              onChange={(event) => onFormChange({ ...form, email: event.target.value })}
            />
          </Field>
          <Field>
            <FieldLabel>{isEdit ? "新密码（留空不变）" : "密码"}</FieldLabel>
            <Input
              value={form.password}
              type="password"
              autoComplete="new-password"
              className="rounded-xl"
              placeholder={isEdit ? "不修改密码" : "至少 8 位"}
              onChange={(event) => onFormChange({ ...form, password: event.target.value })}
            />
          </Field>
          <div className="grid gap-4 md:grid-cols-2">
            <Field>
              <FieldLabel>角色</FieldLabel>
              <Select value={form.role} onValueChange={(value) => onFormChange({ ...form, role: value as AuthRole })}>
                <SelectTrigger className="rounded-xl border-stone-200 bg-white">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="user">普通用户</SelectItem>
                  <SelectItem value="admin">管理员</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel>状态</FieldLabel>
              <label className="flex h-11 items-center gap-3 rounded-xl border border-stone-200 bg-white px-4 text-sm text-stone-700">
                <Checkbox
                  checked={isSelf ? true : form.enabled}
                  disabled={isSelf}
                  onCheckedChange={(checked) => onFormChange({ ...form, enabled: Boolean(checked) })}
                />
                启用账号
              </label>
              {isSelf ? <p className="text-xs text-slate-500">当前会话用户不能在界面中被禁用。</p> : null}
            </Field>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Field>
              <FieldLabel>GGB 余额</FieldLabel>
              <Input
                value={form.image_quota}
                type="number"
                min={0}
                className="rounded-xl"
                onChange={(event) => onFormChange({ ...form, image_quota: event.target.value })}
              />
            </Field>
            <Field>
              <FieldLabel>图片并发</FieldLabel>
              <Input
                value={form.image_concurrency}
                type="number"
                min={form.role === "admin" ? 0 : 1}
                className="rounded-xl"
                onChange={(event) => onFormChange({ ...form, image_concurrency: event.target.value })}
              />
              {form.role === "admin" ? <p className="text-xs text-slate-500">管理员并发会由后端固定为 0。</p> : null}
            </Field>
          </div>
        </div>
        <DialogFooter>
          <Button variant="secondary" className="rounded-xl" disabled={isSubmitting} onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button className="rounded-xl bg-slate-950 text-white hover:bg-slate-800" disabled={isSubmitting} onClick={onSubmit}>
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            {isEdit ? "保存修改" : "创建用户"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
