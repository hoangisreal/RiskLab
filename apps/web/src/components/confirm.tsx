"use client";
import { useRef, useState } from "react";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from "@/components/ui/alert-dialog";
export function useConfirmation() {
  const [message, setMessage] = useState("");
  const trigger = useRef<HTMLElement | null>(null);
  const resolve = useRef<(value: boolean) => void>(() => {});
  function finish(value: boolean) {
    setMessage("");
    resolve.current(value);
  }
  return {
    confirm: (text: string) =>
      new Promise<boolean>((r) => {
        trigger.current =
          document.activeElement instanceof HTMLElement
            ? document.activeElement
            : null;
        resolve.current = r;
        setMessage(text);
      }),
    dialog: (
      <AlertDialog
        open={!!message}
        onOpenChange={(open) => {
          if (!open) finish(false);
        }}
      >
        <AlertDialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            trigger.current?.focus();
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>Xác nhận thao tác</AlertDialogTitle>
            <AlertDialogDescription>{message}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => finish(false)}>
              Hủy
            </AlertDialogCancel>
            <AlertDialogAction onClick={() => finish(true)}>
              Xác nhận
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    ),
  };
}
