export class VisibleMapScheduler {
    private targets = new Map<string, HTMLElement>();
    private visible = new Set<string>();
    private active = new Set<string>();
    private observer?: IntersectionObserver;
    private creationFrame?: number;
    private visibilityFrame?: number;
    private generation = 0;
    private destroyed = false;
    private readonly onViewportChange = () => this.scheduleVisibilityCheck();

    constructor(
        private readonly createMap: (target: HTMLElement) => void,
        private readonly hideMap: (id: string) => void,
    ) {
        window.addEventListener('scroll', this.onViewportChange, {capture: true, passive: true});
        window.addEventListener('resize', this.onViewportChange, {passive: true});
    }

    observe(elements: HTMLElement[]): void {
        this.reset();
        if (this.destroyed) return;
        this.targets = new Map(elements.map(element => [element.id, element]));
        const generation = this.generation;
        if (typeof IntersectionObserver !== 'undefined') {
            this.observer = new IntersectionObserver(entries => {
                if (this.destroyed || generation !== this.generation) return;
                for (const entry of entries) {
                    this.setVisibility((entry.target as HTMLElement).id, entry.isIntersecting);
                }
                this.scheduleCreation();
            });
            elements.forEach(element => this.observer!.observe(element));
        } else {
            this.scheduleVisibilityCheck();
        }
    }

    reset(): void {
        this.generation++;
        this.observer?.disconnect();
        this.observer = undefined;
        if (this.creationFrame !== undefined) cancelAnimationFrame(this.creationFrame);
        if (this.visibilityFrame !== undefined) cancelAnimationFrame(this.visibilityFrame);
        this.creationFrame = undefined;
        this.visibilityFrame = undefined;
        this.targets.clear();
        this.visible.clear();
        this.active.forEach(id => this.hideMap(id));
        this.active.clear();
    }

    destroy(): void {
        this.destroyed = true;
        this.reset();
        window.removeEventListener('scroll', this.onViewportChange, true);
        window.removeEventListener('resize', this.onViewportChange);
    }

    private scheduleVisibilityCheck(): void {
        if (this.destroyed || this.observer || this.visibilityFrame !== undefined) return;
        this.visibilityFrame = requestAnimationFrame(() => {
            this.visibilityFrame = undefined;
            this.targets.forEach(element => {
                const rect = element.getBoundingClientRect();
                this.setVisibility(element.id, rect.width > 0 && rect.height > 0 &&
                    rect.bottom > 0 && rect.top < window.innerHeight &&
                    rect.right > 0 && rect.left < window.innerWidth);
            });
            this.scheduleCreation();
        });
    }

    private setVisibility(id: string, visible: boolean): void {
        if (visible) {
            if (this.targets.has(id)) this.visible.add(id);
        } else {
            this.visible.delete(id);
            if (this.active.delete(id)) this.hideMap(id);
        }
    }

    private scheduleCreation(): void {
        if (this.destroyed || this.creationFrame !== undefined) return;
        const nextId = [...this.visible].find(id => !this.active.has(id));
        if (!nextId) return;
        const generation = this.generation;
        this.creationFrame = requestAnimationFrame(() => {
            this.creationFrame = undefined;
            if (this.destroyed || generation !== this.generation) return;
            const target = this.targets.get(nextId);
            if (target?.isConnected && this.visible.has(nextId)) {
                this.createMap(target);
                this.active.add(nextId);
            } else {
                this.visible.delete(nextId);
            }
            this.scheduleCreation();
        });
    }
}
