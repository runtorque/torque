import {
  closestCenter,
  pointerWithin,
  type Collision,
  type CollisionDetection,
} from '@dnd-kit/core';
import { CSS, type Transform } from '@dnd-kit/utilities';

const LANE_PREFIX = 'lane:';

/**
 * Sortable transforms may scale an item toward the rectangle it is replacing.
 * Board cards have variable heights, so retain each card's intrinsic dimensions
 * and apply only the positional part of the transform.
 */
export function boardCardTransform(transform: Transform | null): string | undefined {
  return CSS.Translate.toString(transform);
}

export function preferredBoardCollisions(collisions: Collision[], activeId: string): Collision[] {
  const candidates = collisions.filter(({ id }) => String(id) !== activeId);
  const cards = candidates.filter(({ id }) => !String(id).startsWith(LANE_PREFIX));
  if (cards.length) return cards;
  const lanes = candidates.filter(({ id }) => String(id).startsWith(LANE_PREFIX));
  return lanes.length ? lanes : candidates;
}

interface AxisCandidate {
  id: string;
  start: number;
  end: number;
}

export function closestAxisCandidate(candidates: AxisCandidate[], coordinate: number): string {
  return [...candidates]
    .sort((left, right) => {
      const leftDistance = coordinate < left.start
        ? left.start - coordinate
        : coordinate > left.end ? coordinate - left.end : 0;
      const rightDistance = coordinate < right.start
        ? right.start - coordinate
        : coordinate > right.end ? coordinate - right.end : 0;
      return leftDistance - rightDistance
        || Math.abs(coordinate - (left.start + left.end) / 2) - Math.abs(coordinate - (right.start + right.end) / 2);
    })[0]?.id ?? '';
}

function sortableLaneId(data: Record<string, unknown> | undefined): string {
  const sortable = data?.sortable;
  if (!sortable || typeof sortable !== 'object') return '';
  const containerId = (sortable as { containerId?: unknown }).containerId;
  return typeof containerId === 'string' ? containerId : '';
}

/**
 * Prefer the card directly beneath the pointer. Card-center collision becomes
 * sticky after sortable siblings move, especially when cards have different
 * heights. Lane bodies remain the fallback for empty space, while
 * closest-center keeps keyboard dragging and the one-pixel lane seam usable.
 */
export const boardCollisionDetection: CollisionDetection = (args) => {
  const pointerCollisions = pointerWithin(args);
  if (args.pointerCoordinates) {
    const laneId = closestAxisCandidate(
      args.droppableContainers.flatMap((container) => {
        const id = String(container.id);
        const rect = args.droppableRects.get(container.id);
        return id.startsWith(LANE_PREFIX) && rect
          ? [{ id, start: rect.left, end: rect.right }]
          : [];
      }),
      args.pointerCoordinates.x,
    );

    if (laneId) {
      const containers = new Map(args.droppableContainers.map((container) => [String(container.id), container]));
      const cardsUnderPointer = pointerCollisions.filter(({ id }) => {
        const candidateId = String(id);
        if (candidateId === String(args.active.id) || candidateId.startsWith(LANE_PREFIX)) return false;
        return sortableLaneId(containers.get(candidateId)?.data.current) === laneId;
      });
      if (cardsUnderPointer.length) return cardsUnderPointer;

      // Lane padding and the one-pixel divider should preserve the pointer's
      // vertical intent. Falling back to the lane itself means "append", which
      // made cards jump to the bottom whenever the cursor left a card by a few
      // pixels horizontally.
      const closestCardId = closestAxisCandidate(
        args.droppableContainers.flatMap((container) => {
          const id = String(container.id);
          const rect = args.droppableRects.get(container.id);
          return id !== String(args.active.id)
            && !id.startsWith(LANE_PREFIX)
            && sortableLaneId(container.data.current) === laneId
            && rect
            ? [{ id, start: rect.top, end: rect.bottom }]
            : [];
        }),
        args.pointerCoordinates.y,
      );
      if (closestCardId) {
        const container = containers.get(closestCardId);
        const rect = container ? args.droppableRects.get(container.id) : undefined;
        if (container && rect) {
          return [{
            id: container.id,
            data: {
              droppableContainer: container,
              value: Math.abs(args.pointerCoordinates.y - (rect.top + rect.bottom) / 2),
            },
          }];
        }
      }

      const laneCollision = pointerCollisions.find(({ id }) => String(id) === laneId);
      if (laneCollision) return [laneCollision];
      const laneContainer = containers.get(laneId);
      if (laneContainer) return [{ id: laneContainer.id, data: { droppableContainer: laneContainer, value: 0 } }];
    }
  }
  if (pointerCollisions.length) {
    return preferredBoardCollisions(pointerCollisions, String(args.active.id));
  }
  return preferredBoardCollisions(closestCenter(args), String(args.active.id));
};

interface DropGeometry {
  activeTop: number;
  activeHeight: number;
  overTop: number;
  overHeight: number;
}

export type DragLaneOrders = Record<string, string[]>;

export function dragLaneForTask(orders: DragLaneOrders, taskId: string): string {
  return Object.keys(orders).find((lane) => orders[lane]?.includes(taskId)) ?? '';
}

export function moveTaskBetweenDragLanes(
  orders: DragLaneOrders,
  taskId: string,
  sourceLane: string,
  targetLane: string,
  position: number,
): DragLaneOrders {
  if (!orders[sourceLane] || !orders[targetLane] || sourceLane === targetLane) return orders;
  const source = orders[sourceLane].filter((id) => id !== taskId);
  const target = orders[targetLane].filter((id) => id !== taskId);
  target.splice(Math.max(0, Math.min(position, target.length)), 0, taskId);
  return { ...orders, [sourceLane]: source, [targetLane]: target };
}

/** Return the server insertion index after removing the active task. */
export function taskDropPosition(
  orderedIds: string[],
  activeId: string,
  overId: string,
  geometry?: DropGeometry,
): number | null {
  const withoutActive = orderedIds.filter((id) => id !== activeId);
  const targetIndex = withoutActive.indexOf(overId);
  if (targetIndex < 0) return null;

  const placeAfter = geometry
    ? geometry.activeTop + geometry.activeHeight / 2
      >= geometry.overTop + geometry.overHeight / 2
    : orderedIds.indexOf(activeId) >= 0
      && orderedIds.indexOf(activeId) < orderedIds.indexOf(overId);

  return Math.min(withoutActive.length, targetIndex + (placeAfter ? 1 : 0));
}
