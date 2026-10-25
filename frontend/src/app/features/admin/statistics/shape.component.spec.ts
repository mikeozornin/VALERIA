import { TASK_SHAPES } from './shape.component';

describe('TASK_SHAPES', () => {
    it('gives at least 10 task types distinct shapes, none of them «?»', () => {
        expect(TASK_SHAPES.length).toBeGreaterThanOrEqual(10);
        expect(new Set(TASK_SHAPES).size).toBe(TASK_SHAPES.length);
        expect(TASK_SHAPES).not.toContain('question');
        expect(TASK_SHAPES.slice(0, 5)).toEqual(['triangle', 'square', 'diamond', 'down', 'circle']);
    });
});
