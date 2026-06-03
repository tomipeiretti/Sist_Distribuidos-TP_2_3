#!/usr/bin/env bash
# Cheat-sheet de diagnostico K8s para la Estacion 5.
# No corre nada destructivo automaticamente: muestra los comandos.

cat <<'EOF'
+---------------------------------------------------------------+
| ARBOL DE CHEQUEOS (usar EN ORDEN cuando algo no funciona)     |
+---------------------------------------------------------------+

1) Pod corriendo?
   kubectl get pods -A
   kubectl describe pod <nombre>            # eventos + reason
   kubectl get pods -l app=catalogo

2) Logs?
   kubectl logs <pod> --tail=50
   kubectl logs <pod> --previous            # del contenedor que crasheo
   kubectl logs -f -l app=pedidos           # streaming por label

3) Service tiene endpoints?
   kubectl get svc
   kubectl get endpoints <svc>              # vacio = selectores no matchean

4) DNS interno funciona?
   kubectl run debug --rm -it --image=busybox:1.36 -- sh
     # dentro: nslookup catalogo
     #         nc -zv catalogo 50051

5) Estado del deploy?
   kubectl rollout status deployment/<name>
   kubectl rollout history deployment/<name>
   kubectl rollout undo deployment/<name>

6) Port-forward para inspeccion local:
   kubectl port-forward svc/pedidos  8000:8000
   kubectl port-forward svc/rabbitmq 15672:15672
   # -> http://localhost:15672  (guest/guest)

+---------------------------------------------------------------+
| ESCENARIOS DE PRUEBA DE RESILIENCIA                           |
+---------------------------------------------------------------+

A) Auto-healing: matar un pod y ver que K8s lo recrea.
   kubectl delete pod -l app=catalogo
   kubectl get pods -l app=catalogo -w

B) Persistencia de mensajes: bajar consumer, publicar, ver pendiente.
   kubectl scale deployment notificaciones --replicas=0
   # crear pedido con scripts/demo.sh
   # abrir http://localhost:15672 -> Queues -> emails -> deberia tener N mensajes
   kubectl scale deployment notificaciones --replicas=1
   kubectl logs -f -l app=notificaciones

C) Cascade prevention: bajar catalogo, verificar que pedidos falla rapido.
   kubectl scale deployment catalogo --replicas=0
   curl -X POST http://localhost:8000/orders \
     -H "Content-Type: application/json" \
     -d '{"sku":"SKU-001","cantidad":1}'
   # debe devolver 503 en <500ms (timeout gRPC=300ms), no colgarse.
   kubectl scale deployment catalogo --replicas=2

EOF
