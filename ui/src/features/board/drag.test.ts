import { describe, expect, it } from 'vitest';

import {
  boardCardTransform,
  closestAxisCandidate,
  dragLaneForTask,
  moveTaskBetweenDragLanes,
  preferredBoardCollisions,
  taskDropPosition,
} from './drag';

describe('Board drag positioning', () => {
  const ids = ['a', 'b', 'c', 'd'];

  it('translates sortable cards without resizing them to the target card', () => {
    expect(boardCardTransform({ x: 18, y: 42, scaleX: 0.7, scaleY: 1.8 }))
      .toBe('translate3d(18px, 42px, 0)');
    expect(boardCardTransform(null)).toBeUndefined();
  });

  it('keeps advancing through consecutive cards when dragging downward', () => {
    expect(taskDropPosition(ids, 'a', 'b', {
      activeTop: 120,
      activeHeight: 40,
      overTop: 100,
      overHeight: 40,
    })).toBe(1);
    expect(taskDropPosition(ids, 'a', 'c', {
      activeTop: 180,
      activeHeight: 40,
      overTop: 160,
      overHeight: 40,
    })).toBe(2);
  });

  it('places a task before the hovered card while dragging upward', () => {
    expect(taskDropPosition(ids, 'd', 'b', {
      activeTop: 70,
      activeHeight: 40,
      overTop: 100,
      overHeight: 40,
    })).toBe(1);
  });

  it('uses source direction for keyboard dragging without pointer geometry', () => {
    expect(taskDropPosition(ids, 'a', 'c')).toBe(2);
    expect(taskDropPosition(ids, 'd', 'b')).toBe(1);
  });

  it('returns null for a target outside the destination lane', () => {
    expect(taskDropPosition(ids, 'a', 'missing')).toBeNull();
  });

  it('transfers the active task into a destination sortable order', () => {
    const moved = moveTaskBetweenDragLanes(
      { Backlog: ['a', 'b'], 'To Do': ['c', 'd'] },
      'a',
      'Backlog',
      'To Do',
      1,
    );

    expect(moved).toEqual({ Backlog: ['b'], 'To Do': ['c', 'a', 'd'] });
    expect(dragLaneForTask(moved, 'a')).toBe('To Do');
  });

  it('uses the lane instead of the active card at a lane boundary', () => {
    const collisions = [
      { id: 'a', data: {} },
      { id: 'lane:To Do', data: {} },
    ];

    expect(preferredBoardCollisions(collisions, 'a').map(({ id }) => id)).toEqual(['lane:To Do']);
  });

  it('still prefers a destination card over its containing lane', () => {
    const collisions = [
      { id: 'a', data: {} },
      { id: 'lane:To Do', data: {} },
      { id: 'b', data: {} },
    ];

    expect(preferredBoardCollisions(collisions, 'a').map(({ id }) => id)).toEqual(['b']);
  });

  it('resolves the nearest lane across a narrow divider seam', () => {
    const lanes = [
      { id: 'lane:Backlog', start: 0, end: 299 },
      { id: 'lane:To Do', start: 301, end: 600 },
    ];

    expect(closestAxisCandidate(lanes, 298)).toBe('lane:Backlog');
    expect(closestAxisCandidate(lanes, 300.75)).toBe('lane:To Do');
    expect(closestAxisCandidate(lanes, 302)).toBe('lane:To Do');
  });

  it('keeps vertical card intent while the pointer is in lane padding', () => {
    const cards = [
      { id: 'first', start: 50, end: 110 },
      { id: 'second', start: 118, end: 178 },
      { id: 'third', start: 186, end: 246 },
    ];

    expect(closestAxisCandidate(cards, 116)).toBe('second');
    expect(closestAxisCandidate(cards, 181)).toBe('second');
    expect(closestAxisCandidate(cards, 190)).toBe('third');
  });
});
