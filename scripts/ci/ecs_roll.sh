# Shared ECS deploy helpers for deploy-aws.yml — source this, don't execute it.
# Requires: aws + jq on PATH, and scripts/ci/retry.sh sourced first (retry()).
#
# One implementation of the describe → image-swap → register → update →
# wait → verify roll, so the migrate, api/worker, and frontend steps cannot
# silently diverge (PR #1365 review). Values are returned via shell variables
# assigned INSIDE the retried functions — retry() logs ::warning:: lines to
# stdout, so `var=$(retry fn)` would swallow them into the captured value.

# Register a new revision of task-definition family $1 with its (single)
# container's image swapped to $2. Sets ECS_REGISTERED_ARN.
ecs_register_revision() {
  local family=$1 image=$2 td new_td
  td=$(aws ecs describe-task-definition --task-definition "$family" \
         --query taskDefinition --output json)
  # del(): the output-only fields describe returns that register rejects.
  new_td=$(jq --arg img "$image" \
    '.containerDefinitions[0].image = $img
     | del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
           .compatibilities, .registeredAt, .registeredBy, .deregisteredAt)' \
    <<<"$td")
  _ecs_register() {
    ECS_REGISTERED_ARN=$(aws ecs register-task-definition --cli-input-json "$new_td" \
      --query 'taskDefinition.taskDefinitionArn' --output text)
  }
  retry _ecs_register
}

# Roll service $2 on cluster $1 to image $3 (service name == family name in
# this stack). Sets ECS_REGISTERED_ARN to the revision it rolled to.
ecs_roll_service() {
  local cluster=$1 service=$2 image=$3
  ecs_register_revision "$service" "$image"
  _ecs_update() {
    aws ecs update-service --cluster "$cluster" --service "$service" \
      --task-definition "$ECS_REGISTERED_ARN" \
      --query 'service.serviceName' --output text >/dev/null
  }
  retry _ecs_update
  echo "$service -> $ECS_REGISTERED_ARN"
}

# Wait for the listed services on cluster $1 to stabilise. TWO waiter laps
# (each 15s x 40 = 10 min): a perfectly normal rollout can exceed one lap
# because the ALB target group's default 300s deregistration drain sits on
# the deployment-complete path — a converging deploy must not spuriously
# fail on the waiter's fixed cap (PR #1365 review).
ecs_wait_stable() {
  local cluster=$1
  shift
  if ! aws ecs wait services-stable --cluster "$cluster" --services "$@"; then
    echo "::warning::services-stable waiter lap 1 timed out — one more lap (ALB drain can push a normal rollout past 10 min)"
    aws ecs wait services-stable --cluster "$cluster" --services "$@"
  fi
}

# Verify service $2's PRIMARY deployment on cluster $1 actually runs image $3:
# an ECS deployment-circuit-breaker rollback to the old image reports stable,
# and "stable on the old code" must fail the deploy, not pass it.
ecs_verify_image() {
  local cluster=$1 service=$2 image=$3 live_td live_img
  live_td=$(aws ecs describe-services --cluster "$cluster" --services "$service" \
              --query 'services[0].deployments[0].taskDefinition' --output text)
  live_img=$(aws ecs describe-task-definition --task-definition "$live_td" \
               --query 'taskDefinition.containerDefinitions[0].image' --output text)
  if [[ "$live_img" != "$image" ]]; then
    echo "::error::$service image mismatch after rollout: $live_img (wanted $image)"
    return 1
  fi
}
