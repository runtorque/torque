import type { ReactNode } from 'react';
import {
  Button as AriaButton,
  Dialog,
  DialogTrigger,
  Heading,
  Menu,
  MenuItem,
  MenuTrigger,
  Modal,
  ModalOverlay,
  Popover,
  Text,
  type ButtonProps as AriaButtonProps,
  type MenuItemProps,
} from 'react-aria-components';

import styles from './primitives.module.css';

export interface ButtonProps extends Omit<AriaButtonProps, 'className'> {
  className?: string;
  tone?: 'default' | 'primary' | 'danger' | 'quiet';
}

export function Button({ tone = 'default', className, ...props }: ButtonProps) {
  return (
    <AriaButton
      {...props}
      className={`${styles.button ?? ''} ${styles[`button_${tone}`] ?? ''} ${className ?? ''}`}
    />
  );
}

interface ModalDialogProps {
  children: ReactNode;
  title: string;
  description?: string;
  trigger?: ReactNode;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  size?: 'small' | 'medium' | 'large' | 'wide';
  bodyLayout?: 'scroll' | 'fit';
}

export function ModalDialog({
  children,
  title,
  description,
  trigger,
  isOpen,
  onOpenChange,
  size = 'medium',
  bodyLayout = 'scroll',
}: ModalDialogProps) {
  const overlay = (
      <ModalOverlay
        className={styles.overlay ?? ''}
        isDismissable
        {...(trigger || isOpen === undefined ? {} : { isOpen })}
        {...(trigger || !onOpenChange ? {} : { onOpenChange })}
      >
        <Modal className={`${styles.modal ?? ''} ${styles[`modal_${size}`] ?? ''} ${bodyLayout === 'fit' ? styles.modal_fit ?? '' : ''}`}>
          <Dialog aria-label={title} className={styles.dialog ?? ''}>
            {({ close }) => (
              <>
                <header className={styles.dialogHeader ?? ''}>
                  <div>
                    <Heading slot="title" className={styles.dialogTitle ?? ''}>{title}</Heading>
                    {description ? <Text slot="description" className={styles.dialogDescription ?? ''}>{description}</Text> : null}
                  </div>
                  <Button tone="quiet" aria-label="Close dialog" onPress={close}>×</Button>
                </header>
                <div className={`${styles.dialogBody ?? ''} ${bodyLayout === 'fit' ? styles.dialogBody_fit ?? '' : ''}`} data-dialog-body-layout={bodyLayout}>{children}</div>
              </>
            )}
          </Dialog>
        </Modal>
      </ModalOverlay>
  );
  if (!trigger) return overlay;
  return (
    <DialogTrigger {...(isOpen === undefined ? {} : { isOpen })} {...(onOpenChange ? { onOpenChange } : {})}>
      {trigger}
      {overlay}
    </DialogTrigger>
  );
}

interface ActionMenuProps {
  label: string;
  children: ReactNode;
  trigger?: ReactNode;
}

export function ActionMenu({ label, children, trigger }: ActionMenuProps) {
  return (
    <MenuTrigger>
      {trigger ?? <Button tone="quiet" aria-label={label}>•••</Button>}
      <Popover className={styles.popover ?? ''} placement="bottom end">
        <Menu className={styles.menu ?? ''} aria-label={label}>{children}</Menu>
      </Popover>
    </MenuTrigger>
  );
}

export function ActionMenuItem(props: MenuItemProps) {
  return <MenuItem {...props} className={styles.menuItem ?? ''} />;
}

interface StateSurfaceProps {
  title: string;
  description: string;
  action?: ReactNode;
  tone?: 'neutral' | 'danger';
}

export function StateSurface({ title, description, action, tone = 'neutral' }: StateSurfaceProps) {
  return (
    <section className={`${styles.stateSurface ?? ''} ${styles[`stateSurface_${tone}`] ?? ''}`}>
      <strong>{title}</strong>
      <p>{description}</p>
      {action}
    </section>
  );
}
