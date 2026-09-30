import { useState } from 'react';
import { Button, ModalDialog } from '../design/primitives';
import type { CommandSender } from '../features/board/BoardPanel';
import type { TorqueCommand } from '../protocol';
import styles from './App.module.css';

export interface ConfirmedCommand {
  title: string;
  message: string;
  confirmLabel: string;
  command: TorqueCommand;
}

/** Shell commands share the same reviewed confirmation in browser and desktop. */
export function CommandConfirmation({ request, sendCommand, onClose }: {
  request: ConfirmedCommand;
  sendCommand: CommandSender;
  onClose: () => void;
}) {
  const [unavailable, setUnavailable] = useState(false);
  return <ModalDialog title={request.title} description={request.message} size="small" isOpen onOpenChange={(open) => { if (!open) onClose(); }}>
    <div className={styles.nativeForm}>
      {unavailable ? <p role="alert">Torque is not connected. Your change was not sent. Reconnect before trying again.</p> : null}
      <footer>
        <Button tone="quiet" autoFocus onPress={onClose}>Cancel</Button>
        <Button tone="danger" onPress={() => {
          if (sendCommand(request.command)) onClose();
          else setUnavailable(true);
        }}>{request.confirmLabel}</Button>
      </footer>
    </div>
  </ModalDialog>;
}
