import { useEffect, useState } from 'react';
import { Modal, ModalCancelButton } from '../../components/Modal';
import { Switch } from '../../components/Switch';
import { A2AOutboundTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';

interface Props {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}

export function A2ADiscoverySettingsModal({ open, onClose, onSaved }: Props) {
  const [allowHttp, setAllowHttp] = useState(false);
  const [allowPrivateNetwork, setAllowPrivateNetwork] = useState(false);
  const [allowPublicHttp, setAllowPublicHttp] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    void A2AOutboundTemplateApi.getDiscoverySettings()
      .then((settings) => {
        if (cancelled) return;
        setAllowHttp(settings.allow_http);
        setAllowPrivateNetwork(settings.allow_private_network);
        setAllowPublicHttp(settings.allow_public_http);
      })
      .catch((error) => {
        if (cancelled) return;
        toast('danger', error instanceof ApiError ? error.detail : (error as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [open]);

  const save = async () => {
    setSaving(true);
    try {
      await A2AOutboundTemplateApi.updateDiscoverySettings({
        allow_http: allowHttp,
        allow_private_network: allowPrivateNetwork,
        allow_public_http: allowPublicHttp,
      });
      toast('success', '网络访问设置已保存');
      onSaved();
    } catch (error) {
      toast('danger', error instanceof ApiError ? error.detail : (error as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title="网络访问设置"
      onClose={onClose}
      footer={<><ModalCancelButton /><button className="btn primary" disabled={loading || saving} onClick={() => void save()}>保存</button></>}
    >
      <div className="flex flex-col gap-4">
        <p className="text-xs text-muted">统一用于发现注册、Token 验证、向 Gateway 同步凭据和 Gateway 任务派发。保存后同步到已关联的 Gateway。</p>
        <div className="flex items-start justify-between gap-4">
          <div><div className="font-medium">允许 HTTP 地址</div><div className="mt-1 text-xs text-muted">HTTP 总开关。使用内网或公网 HTTP 地址时，还需开启对应的地址开关。</div></div>
          <Switch checked={allowHttp} disabled={loading} aria-label="允许 HTTP 地址" onChange={setAllowHttp} />
        </div>
        <div className="flex items-start justify-between gap-4">
          <div><div className="font-medium">允许内网地址</div><div className="mt-1 text-xs text-muted">允许访问 10.x、172.16-31.x、192.168.x 内网地址。Manager 和 Gateway 均需能连接目标服务。</div></div>
          <Switch checked={allowPrivateNetwork} disabled={loading} aria-label="允许内网地址" onChange={setAllowPrivateNetwork} />
        </div>
        <div className="flex items-start justify-between gap-4">
          <div><div className="font-medium">允许公网 HTTP</div><div className="mt-1 text-xs text-muted">与 HTTP 地址开关同时开启时，允许通过公网 HTTP 地址发现和调用 A2A Agent。</div></div>
          <Switch checked={allowPublicHttp} disabled={loading} aria-label="允许公网 HTTP" onChange={setAllowPublicHttp} />
        </div>
        <div className="rounded-md bg-warning/10 px-3 py-2 text-xs text-warning">仅建议用于可信联调网络。企业版始终禁止访问 127.0.0.1、::1 和 localhost 等回环地址。</div>
      </div>
    </Modal>
  );
}
