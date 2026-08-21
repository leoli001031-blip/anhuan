// 统一错误分类：HTTP 状态码 → 互不相同的 UI 状态。
// 原始错误码/堆栈永不直接展示，只映射为业务可读文案。

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(status: number, code: string, retryable: boolean) {
    super(code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

export type ErrorKind =
  | "unauthenticated" // 401
  | "forbidden" // 403
  | "notFound" // 404
  | "conflict" // 409
  | "invalid" // 422
  | "unavailable" // 503 或显式未接入
  | "network" // 网络层失败
  | "unknown";

export function errorKind(error: unknown): ErrorKind {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        return "unauthenticated";
      case 403:
        return "forbidden";
      case 404:
        return "notFound";
      case 409:
        return "conflict";
      case 422:
        return "invalid";
      case 503:
        return "unavailable";
      case 0:
        return "network";
      default:
        return error.status >= 500 ? "unavailable" : "unknown";
    }
  }
  return "unknown";
}

export const ERROR_COPY: Record<
  ErrorKind,
  { title: string; description: string }
> = {
  unauthenticated: {
    title: "登录已失效",
    description: "请重新登录后再继续操作。",
  },
  forbidden: {
    title: "没有访问权限",
    description: "当前账号无权查看此内容，如有疑问请联系服务商。",
  },
  notFound: {
    title: "内容不存在",
    description: "该内容不存在、未发布或已被撤回。",
  },
  conflict: {
    title: "状态已变化",
    description: "该对象刚刚被其他人更新，已为你刷新到最新状态。",
  },
  invalid: {
    title: "输入不符合要求",
    description: "请检查输入内容后重试。",
  },
  unavailable: {
    title: "服务暂时不可用",
    description: "请稍后重试；若持续失败请联系服务商。",
  },
  network: {
    title: "网络连接异常",
    description: "请检查网络连接后重试。",
  },
  unknown: {
    title: "操作未完成",
    description: "发生未预期的错误，请稍后重试。",
  },
};
