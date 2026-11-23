import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import type { ContainerTemplateForm } from '../../utils/containerTemplateForm';
import './ServiceConfigTemplateModal.css';
import './ContainerTemplateModal.css';

interface Props {
  form: ContainerTemplateForm;
  update: <K extends keyof ContainerTemplateForm>(key: K, value: ContainerTemplateForm[K]) => void;
}

type StringField = {
  [K in keyof ContainerTemplateForm]: ContainerTemplateForm[K] extends string ? K : never;
}[keyof ContainerTemplateForm];

function Field({ label, required, wide, hint, children }: {
  label: string; required?: boolean; wide?: boolean; hint?: string; children: ReactNode;
}) {
  return <div className={`runtime-template-field${wide ? ' runtime-template-field--wide' : ''}`}>
    <div className="label">{label}{required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}</div>
    {children}
    {hint && <p className="runtime-template-field__hint">{hint}</p>}
  </div>;
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section className="runtime-template-section">
    <div className="runtime-template-group-label"><h3>{title}</h3>{hint && <p>{hint}</p>}</div>
    <div className="runtime-template-grid">{children}</div>
  </section>;
}

function Disclosure({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  return <details className="runtime-template-disclosure">
    <summary><span className="runtime-template-disclosure__title">{title}</span><span className="runtime-template-disclosure__meta">{hint}</span></summary>
    <div className="runtime-template-disclosure__body runtime-template-grid">{children}</div>
  </details>;
}

/** Shared new/edit fields; source configuration is mapped and preserved by containerTemplateForm. */
export function ContainerTemplateFields({ form, update }: Props) {
  const { t, i18n } = useTranslation();
  const tr = (zh: string, en: string) => i18n.language.startsWith('zh') ? zh : en;
  const id = useId();
  const label = (key: string) => t(`containerTemplate.${key}`);
  const optional = tr('可选配置', 'Optional settings');
  const advanced = tr('高级参数', 'Advanced settings');
  const extraHint = tr('完整 JSON 用于保留和调整扩展字段；如同时修改上方常用字段，则应用上方改动。', 'Use the full JSON to retain and edit extension fields. Changes to the common controls above take precedence when both are edited.');

  const text = (key: StringField, title: string, maxLength?: number, required = false, wide = false, hint?: string, placeholder?: string) => (
    <Field key={key} label={title} required={required} wide={wide} hint={hint}>
      {maxLength
        ? <LimitedTextInput ariaLabel={title} value={form[key]} maxLength={maxLength} required={required} placeholder={placeholder} onChange={value => update(key, value)} />
        : <input className="input" aria-label={title} value={form[key]} required={required} placeholder={placeholder} onChange={event => update(key, event.target.value)} />}
    </Field>
  );
  const json = (key: StringField, title: string, placeholder: string, hint?: string, wide = true) => (
    <Field key={key} label={title} wide={wide} hint={hint}>
      <textarea className="input container-template-json" aria-label={title} value={form[key]} placeholder={placeholder} spellCheck={false} onChange={event => update(key, event.target.value)} />
    </Field>
  );
  const securityProfile = (key: 'seccomp_type' | 'apparmor_type', title: string) => (
    <Field key={key} label={title}>
      <select className="select" aria-label={title} value={form[key]} onChange={event => update(key, event.target.value)}>
        <option value="">{tr('未设置', 'Not set')}</option>
        <option value="RuntimeDefault">RuntimeDefault</option>
        <option value="Unconfined">Unconfined</option>
        <option value="Localhost">Localhost</option>
        {form[key] && !['RuntimeDefault', 'Unconfined', 'Localhost'].includes(form[key]) && <option value={form[key]}>{form[key]}</option>}
      </select>
    </Field>
  );

  return <div className="container-template-fields">
    <Section title={tr('基本信息', 'Basic information')}>
      {text('template_name', label('templateName'), 128, true)}
      {text('description', label('description'), 512)}
    </Section>

    <Section title={tr('镜像与容器', 'Image and container')} hint={tr('定义容器身份与镜像，运行时模板通过容器 ID 引用。', 'Define the container identity and image. Runtime templates reference its container ID.')}>
      {text('container_id', label('containerId'), 100, true, false, label('containerIdHint'))}
      {text('name', label('containerName'), 128, true)}
      {text('image', label('image'), 512, true, true, undefined, 'registry.example.com/agent:version')}
      <Field label={label('imagePullPolicy')}>
        <select className="select" aria-label={label('imagePullPolicy')} value={form.image_pull_policy} onChange={event => update('image_pull_policy', event.target.value)}>
          <option value="IfNotPresent">IfNotPresent</option><option value="Always">Always</option><option value="Never">Never</option>
          {form.image_pull_policy && !['IfNotPresent', 'Always', 'Never'].includes(form.image_pull_policy) && <option value={form.image_pull_policy}>{form.image_pull_policy}</option>}
        </select>
      </Field>
    </Section>

    <Disclosure title={tr('启动与环境', 'Startup and environment')} hint={optional}>
      {json('command_json', tr('启动命令 command', 'Startup command'), '["python", "-m", "app"]', tr('JSON 字符串数组，留空使用镜像默认命令。', 'A JSON string array. Leave empty to use the image default.'), false)}
      {json('args_json', tr('启动参数 args', 'Startup arguments'), '["--port", "8080"]', tr('JSON 字符串数组，按数组顺序传入。', 'A JSON string array, passed in the specified order.'), false)}
      {json('ports_json', label('ports'), '[{"name":"sse","containerPort":8766}]', label('portsHint'))}
      {json('env_text', tr('环境变量', 'Environment variables'), 'KEY=value', tr('支持每行 KEY=value；引用 Secret、ConfigMap 或 Pod 字段时使用 JSON 数组，保留 valueFrom。', 'Use KEY=value per line, or a JSON array to retain valueFrom references to Secrets, ConfigMaps or Pod fields.'))}
      {json('env_from_json', label('envFrom'), '[{"secretRef":{"name":"deployment-secret"}}]')}
    </Disclosure>

    <Disclosure title={tr('资源与存储', 'Resources and storage')} hint={optional}>
      {text('cpu_request', label('cpuRequest'), undefined, false, false, undefined, '100m')}
      {text('memory_request', label('memoryRequest'), undefined, false, false, undefined, '256Mi')}
      {text('cpu_limit', label('cpuLimit'), undefined, false, false, undefined, '2')}
      {text('memory_limit', label('memoryLimit'), undefined, false, false, undefined, '2Gi')}
      {json('volume_mounts_json', label('volumeMounts'), '[{"name":"data","mountPath":"/data"}]', tr('挂载名称需与运行时模板中的 volumes 一致。', 'Mount names must match volumes in the runtime template.'))}
      <details className="runtime-template-disclosure runtime-template-disclosure--nested runtime-template-field--wide">
        <summary><span className="runtime-template-disclosure__title">{tr('扩展资源字段', 'Additional resource fields')}</span><span className="runtime-template-disclosure__meta">JSON</span></summary>
        <div className="runtime-template-disclosure__body">{json('resource_extra_json', tr('完整资源配置（JSON 对象）', 'Full resource configuration (JSON object)'), '{}', extraHint)}</div>
      </details>
    </Disclosure>

    <Disclosure title={tr('安全设置', 'Security settings')} hint={advanced}>
      {(['run_as_user', 'run_as_group'] as const).map((key, index) => <Field key={key} label={label(index ? 'runAsGroup' : 'runAsUser')}>
        <input className="input" aria-label={label(index ? 'runAsGroup' : 'runAsUser')} type="number" min={0} max={2147483647} value={form[key]} onChange={event => update(key, event.target.value)} />
      </Field>)}
      {text('capabilities_add', label('capabilitiesAdd'), undefined, false, true, undefined, 'SYS_ADMIN, NET_ADMIN')}
      {securityProfile('seccomp_type', label('seccompType'))}
      {securityProfile('apparmor_type', label('apparmorType'))}
      <div className="runtime-template-field runtime-template-field--wide">
        <label className="container-template-check" htmlFor={`${id}-privileged`}><input id={`${id}-privileged`} type="checkbox" checked={form.privileged} onChange={event => update('privileged', event.target.checked)} />{label('privileged')}</label>
      </div>
      <details className="runtime-template-disclosure runtime-template-disclosure--nested runtime-template-field--wide">
        <summary><span className="runtime-template-disclosure__title">{tr('扩展安全字段', 'Additional security fields')}</span><span className="runtime-template-disclosure__meta">JSON</span></summary>
        <div className="runtime-template-disclosure__body">{json('security_extra_json', tr('完整安全配置（JSON 对象）', 'Full security configuration (JSON object)'), '{}', extraHint)}</div>
      </details>
    </Disclosure>

    <Disclosure title={tr('就绪探针', 'Readiness probe')} hint={form.probe_enabled ? tr('已配置', 'Configured') : optional}>
      <div className="runtime-template-field runtime-template-field--wide">
        <label className="container-template-check" htmlFor={`${id}-probe`}><input id={`${id}-probe`} type="checkbox" checked={form.probe_enabled} onChange={event => update('probe_enabled', event.target.checked)} />{tr('配置就绪探针', 'Configure a readiness probe')}</label>
        <p className="runtime-template-field__hint">{tr('用于判断容器能否接收请求；默认配置中的探针会完整保留。', 'Determines when the container can accept requests. Probes from default configurations are retained.')}</p>
      </div>
      {form.probe_enabled && <>
        <Field label={label('probeType')}>
          <select className="select" aria-label={label('probeType')} value={form.probe_type} onChange={event => update('probe_type', event.target.value as ContainerTemplateForm['probe_type'])}>
            <option value="httpGet">HTTP</option><option value="tcpSocket">TCP</option><option value="custom">{tr('自定义（exec / gRPC）', 'Custom (exec / gRPC)')}</option>
          </select>
        </Field>
        {form.probe_type !== 'custom' && text('probe_port', label('probePort'), undefined, false, false, tr('支持端口号或已声明的端口名称。', 'Use a port number or a declared port name.'), '8080')}
        {form.probe_type === 'httpGet' && text('probe_path', label('healthPath'), undefined, false, true, undefined, '/health')}
        {([
          ['readiness_initial_delay', 'readinessInitialDelay', 0],
          ['readiness_period', 'readinessPeriod', 1],
          ['readiness_timeout', 'probeTimeout', 1],
        ] as const).map(([key, title, min]) => <Field key={key} label={label(title)}>
          <input className="input" aria-label={label(title)} type="number" min={min} max={2147483647} value={form[key]} onChange={event => update(key, Number(event.target.value))} />
        </Field>)}
        <details className="runtime-template-disclosure runtime-template-disclosure--nested runtime-template-field--wide" open={form.probe_type === 'custom' || undefined}>
          <summary><span className="runtime-template-disclosure__title">{tr('扩展探针字段', 'Additional probe fields')}</span><span className="runtime-template-disclosure__meta">JSON</span></summary>
          <div className="runtime-template-disclosure__body">{json('probe_extra_json', tr('完整探针配置（JSON 对象）', 'Full probe configuration (JSON object)'), '{"exec":{"command":["check-ready"]}}', form.probe_type === 'custom' ? tr('填写 exec 或 grpc 探针及扩展字段；如同时修改上方时间参数，则应用上方改动。', 'Enter an exec or grpc probe and extensions. Changes to the timing controls above take precedence when both are edited.') : extraHint)}</div>
        </details>
      </>}
    </Disclosure>
  </div>;
}
