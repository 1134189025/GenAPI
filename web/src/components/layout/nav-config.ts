import type { AuthRole } from "@/store/auth";

export type NavIcon =
  | "brush"
  | "crown"
  | "gift"
  | "users"
  | "ticket"
  | "sparkles"
  | "server"
  | "bot"
  | "image"
  | "logs"
  | "settings";

export type NavItem = {
  href: string;
  label: string;
  description: string;
  icon: NavIcon;
};

export type NavGroup = {
  label: string;
  items: NavItem[];
};

export type PageMeta = {
  title: string;
  description: string;
};

const userNavigation: NavGroup[] = [
  {
    label: "工作台",
    items: [
      {
        href: "/image",
        label: "生图仪表盘",
        description: "创作、历史、额度和任务队列",
        icon: "brush",
      },
      {
        href: "/membership",
        label: "会员中心",
        description: "会员状态、套餐和周期额度",
        icon: "crown",
      },
      {
        href: "/redeem",
        label: "兑换中心",
        description: "兑换图片额度、并发和会员码",
        icon: "gift",
      },
    ],
  },
];

const adminNavigation: NavGroup[] = [
  ...userNavigation,
  {
    label: "用户与码",
    items: [
      {
        href: "/admin/users",
        label: "用户管理",
        description: "账号、角色、额度和并发",
        icon: "users",
      },
      {
        href: "/admin/redeem-codes",
        label: "兑换码",
        description: "图片额度、并发、会员和邀请码",
        icon: "ticket",
      },
      {
        href: "/admin/membership-plans",
        label: "会员套餐",
        description: "会员套餐、周期额度和排序",
        icon: "crown",
      },
      {
        href: "/admin/promo-codes",
        label: "优惠码",
        description: "注册赠送图片额度",
        icon: "sparkles",
      },
    ],
  },
  {
    label: "运营",
    items: [
      {
        href: "/admin/accounts",
        label: "账号池",
        description: "ChatGPT 账号导入、刷新和状态",
        icon: "server",
      },
      {
        href: "/admin/register-machine",
        label: "注册机",
        description: "批量注册和导入账号",
        icon: "bot",
      },
      {
        href: "/admin/images",
        label: "图片管理",
        description: "本地生成图片归档",
        icon: "image",
      },
      {
        href: "/admin/logs",
        label: "日志",
        description: "调用、账号和系统事件",
        icon: "logs",
      },
      {
        href: "/admin/settings",
        label: "系统设置",
        description: "基础配置、SMTP、注册和导入源",
        icon: "settings",
      },
    ],
  },
];

const pageMeta: Record<string, PageMeta> = {};
for (const group of adminNavigation) {
  for (const item of group.items) {
    pageMeta[item.href] = {
      title: item.label,
      description: item.description,
    };
  }
}

Object.assign(pageMeta, {
  "/": { title: "进入系统", description: "检查登录状态并跳转" },
  "/login": { title: "登录", description: "使用邮箱和密码进入控制台" },
  "/register": { title: "注册", description: "创建账号并领取图片额度" },
  "/setup": { title: "初始化", description: "创建首个管理员账号" },
  "/accounts": pageMeta["/admin/accounts"],
  "/image-manager": pageMeta["/admin/images"],
  "/logs": pageMeta["/admin/logs"],
  "/settings": pageMeta["/admin/settings"],
});

export function getNavigationGroups(role: AuthRole): NavGroup[] {
  return role === "admin" ? adminNavigation : userNavigation;
}

export function normalizeDashboardPath(pathname: string) {
  if (pathname.startsWith("/admin/accounts")) return "/admin/accounts";
  if (pathname.startsWith("/admin/images")) return "/admin/images";
  if (pathname.startsWith("/admin/logs")) return "/admin/logs";
  if (pathname.startsWith("/admin/settings")) return "/admin/settings";
  if (pathname.startsWith("/accounts")) return "/admin/accounts";
  if (pathname.startsWith("/image-manager")) return "/admin/images";
  if (pathname.startsWith("/logs")) return "/admin/logs";
  if (pathname.startsWith("/settings")) return "/admin/settings";
  const match = Object.keys(pageMeta)
    .filter((path) => path !== "/" && pathname.startsWith(path))
    .sort((a, b) => b.length - a.length)[0];
  return match || pathname || "/";
}

export function getPageMeta(pathname: string): PageMeta {
  return pageMeta[normalizeDashboardPath(pathname)] || {
    title: "控制台",
    description: "Genapi 管理后台",
  };
}

export function isPublicAppRoute(pathname: string) {
  return pathname === "/" || ["/login", "/register", "/setup"].some((path) => pathname.startsWith(path));
}
