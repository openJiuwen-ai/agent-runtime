import { create } from 'zustand';

/** 引导跳转后由目标页面自动打开的添加弹框 */
export type GuideOpenTarget =
  | 'agentTemplateNew'
  | 'modelTemplateNew'
  | 'instanceCreate'
  | 'accessUsers'
  | 'accessOrgs'
  | 'agentResourceAdd'
  | 'serviceResourceAdd';

interface GuideState {
  /** 引导相关资源（模板/准入/资源分配）增删后自增，驱动引导状态自动刷新 */
  revision: number;
  /** 待自动打开的添加弹框（引导跳转用），由目标页面消费后清除 */
  openTarget: GuideOpenTarget | null;
  bump: () => void;
  requestOpen: (target: GuideOpenTarget) => void;
  consumeOpen: () => void;
}

export const useGuideStore = create<GuideState>((set) => ({
  revision: 0,
  openTarget: null,
  bump: () => set((s) => ({ revision: s.revision + 1 })),
  requestOpen: (target) => set({ openTarget: target }),
  consumeOpen: () => set({ openTarget: null }),
}));

/** 资源增删成功后调用，通知所有引导状态 hook 重新查询 */
export function bumpGuideRevision() {
  useGuideStore.getState().bump();
}

/** 记录「跳转后要自动打开的添加弹框」，由目标页面消费 */
export function requestGuideOpen(target: GuideOpenTarget) {
  useGuideStore.getState().requestOpen(target);
}
