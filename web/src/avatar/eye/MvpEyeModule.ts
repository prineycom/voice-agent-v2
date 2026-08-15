import {
  AVATAR_HOST_INTERFACE_VERSION,
  AVATAR_REQUIRED_CAPABILITIES,
  type AvatarModuleFailureHandlerV1,
  type AvatarModuleV1,
  type ValidatedAvatarControlV1,
} from '../contract'
import { computeEyeRenderState, type EyePupilPosition } from './eyeModel'
import './mvpEye.css'

const SVG_NS = 'http://www.w3.org/2000/svg'

function svgElement<K extends keyof SVGElementTagNameMap>(
  name: K,
  attributes: Record<string, string> = {},
): SVGElementTagNameMap[K] {
  const element = document.createElementNS(SVG_NS, name)
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value)
  return element
}

/** Original repository-authored deterministic SVG eye renderer. */
export class MvpEyeModule implements AvatarModuleV1 {
  readonly manifest = {
    interfaceVersion: AVATAR_HOST_INTERFACE_VERSION,
    id: 'mvp-eye-svg-v1',
    displayName: 'Deterministic MVP Eye',
    capabilities: AVATAR_REQUIRED_CAPABILITIES,
    deterministic: true,
  } as const

  private root: HTMLDivElement | null = null
  private eyeGroup: SVGGElement | null = null
  private irisGroup: SVGGElement | null = null
  private outerGroup: SVGGElement | null = null
  private loader: SVGCircleElement | null = null
  private regularPupil: SVGCircleElement | null = null
  private control: ValidatedAvatarControlV1 | null = null
  private lastPupilPosition: EyePupilPosition | null = null
  private interruptionOrigin: EyePupilPosition | null = null
  private animationFrame: number | null = null
  private failureHandler: AvatarModuleFailureHandlerV1 | null = null

  setFailureHandler(handler: AvatarModuleFailureHandlerV1 | null): void {
    this.failureHandler = handler
  }

  mount(container: HTMLElement): void {
    if (this.root !== null) throw new Error('MVP eye is already mounted')
    const root = document.createElement('div')
    root.className = 'mvp-eye'
    root.dataset.module = this.manifest.id
    root.setAttribute('role', 'img')
    root.setAttribute('aria-label', 'AI eye avatar')

    const svg = svgElement('svg', {
      class: 'mvp-eye__svg',
      viewBox: '0 0 400 700',
      'aria-hidden': 'true',
      focusable: 'false',
    })
    const defs = svgElement('defs')
    const gradient = svgElement('radialGradient', { id: 'mvp-eye-iris-gradient' })
    gradient.append(
      svgElement('stop', { offset: '0%', 'stop-color': 'currentColor', 'stop-opacity': '0.95' }),
      svgElement('stop', { offset: '62%', 'stop-color': 'currentColor', 'stop-opacity': '0.35' }),
      svgElement('stop', { offset: '100%', 'stop-color': 'currentColor', 'stop-opacity': '0.05' }),
    )
    defs.append(gradient)

    const outer = svgElement('g', { class: 'mvp-eye__outer' })
    outer.append(
      svgElement('circle', { class: 'mvp-eye__orbit mvp-eye__orbit--outer', cx: '200', cy: '350', r: '150' }),
      svgElement('circle', { class: 'mvp-eye__orbit mvp-eye__orbit--middle', cx: '200', cy: '350', r: '124' }),
      svgElement('path', {
        class: 'mvp-eye__ticks',
        d: 'M200 176v22 M200 502v22 M26 350h22 M352 350h22 M77 227l16 16 M307 457l16 16 M77 473l16-16 M307 243l16-16',
      }),
    )

    const eyeGroup = svgElement('g', { class: 'mvp-eye__eye' })
    eyeGroup.append(svgElement('path', {
      class: 'mvp-eye__outline',
      d: 'M74 350 Q200 218 326 350 Q200 482 74 350Z',
    }))
    const iris = svgElement('g', { class: 'mvp-eye__iris-group' })
    iris.append(svgElement('circle', {
      class: 'mvp-eye__iris-halo', cx: '200', cy: '350', r: '74',
    }))
    iris.append(svgElement('circle', {
      class: 'mvp-eye__iris', cx: '200', cy: '350', r: '58', fill: 'url(#mvp-eye-iris-gradient)',
    }))
    const pupil = svgElement('circle', {
      class: 'mvp-eye__pupil', cx: '200', cy: '350', r: '24',
    })
    const loader = svgElement('circle', {
      class: 'mvp-eye__loader', cx: '200', cy: '350', r: '28',
    })
    iris.append(pupil, loader)
    eyeGroup.append(iris)
    svg.append(defs, outer, eyeGroup)
    root.append(svg)
    container.replaceChildren(root)

    this.root = root
    this.eyeGroup = eyeGroup
    this.irisGroup = iris
    this.outerGroup = outer
    this.loader = loader
    this.regularPupil = pupil
    this.animationFrame = requestAnimationFrame(this.render)
  }

  update(input: ValidatedAvatarControlV1): void {
    if (this.root === null) throw new Error('MVP eye is not mounted')
    if (input.lifecycle === 'interrupted' && this.control?.lifecycle !== 'interrupted') {
      this.interruptionOrigin = this.lastPupilPosition
    } else if (input.lifecycle !== 'interrupted') {
      this.interruptionOrigin = null
    }
    this.control = input
    this.root.dataset.motion = input.motion
    this.draw(input.timestampMs)
  }

  cancel(timestampMs: number): void {
    if (this.control === null) return
    if (this.control.lifecycle !== 'interrupted') {
      this.interruptionOrigin = this.lastPupilPosition
    }
    this.control = {
      ...this.control,
      timestampMs,
      lifecycle: 'interrupted',
      trackingTarget: null,
      speechEnvelope: null,
    }
    this.draw(timestampMs)
  }

  dispose(): void {
    if (this.animationFrame !== null) cancelAnimationFrame(this.animationFrame)
    this.animationFrame = null
    this.root?.remove()
    this.root = null
    this.eyeGroup = null
    this.irisGroup = null
    this.outerGroup = null
    this.loader = null
    this.regularPupil = null
    this.control = null
    this.lastPupilPosition = null
    this.interruptionOrigin = null
    this.failureHandler = null
  }

  private readonly render = (timeMs: number): void => {
    try {
      this.draw(timeMs)
      this.animationFrame = this.root === null ? null : requestAnimationFrame(this.render)
    } catch {
      this.animationFrame = null
      this.failureHandler?.({ kind: 'render-loop-failed' })
    }
  }

  private draw(timeMs: number): void {
    if (
      this.control === null || this.root === null || this.eyeGroup === null
      || this.irisGroup === null || this.outerGroup === null
      || this.loader === null || this.regularPupil === null
    ) return
    const state = computeEyeRenderState(this.control, timeMs, this.interruptionOrigin)
    this.root.dataset.state = state.lifecycle
    this.root.setAttribute('aria-label', `AI eye avatar: ${state.lifecycle}`)
    this.eyeGroup.style.transform = `scaleY(${Math.max(0.035, 1 - state.blinkClosure * 0.965)})`
    this.irisGroup.style.transform = `translate(${state.pupilX * 112}px, ${state.pupilY * 112}px)`
    this.outerGroup.style.transform = `scale(${state.outerScale})`
    this.loader.style.transform = `rotate(${state.loaderAngleDegrees}deg)`
    const thinking = state.lifecycle === 'thinking'
    this.loader.style.opacity = thinking ? '1' : '0'
    this.regularPupil.style.opacity = thinking ? '0' : '1'
    this.root.style.setProperty('--mvp-eye-envelope', state.speechEnvelope.toFixed(4))
    this.lastPupilPosition = { pupilX: state.pupilX, pupilY: state.pupilY }
  }
}

export function createMvpEyeModule(): AvatarModuleV1 {
  return new MvpEyeModule()
}
