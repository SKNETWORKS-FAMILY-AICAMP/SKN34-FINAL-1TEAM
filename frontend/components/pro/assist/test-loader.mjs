// 의존성 없이 Node 테스트에서 로컬 TypeScript 상대 import를 읽어요. 제품 번들에는 사용하지 않아요.
export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    if (error.code !== "ERR_MODULE_NOT_FOUND" || !specifier.startsWith(".") ||
      /\.[cm]?[jt]s$/.test(specifier)) throw error;
    return nextResolve(specifier + ".ts", context);
  }
}
